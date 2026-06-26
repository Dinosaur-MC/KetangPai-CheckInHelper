"""签到链路延迟基准测试。

测量从 API 端点入口到首次请求课堂派 API 前的框架+业务逻辑延迟。
所有测试要求 median 延迟 < 50ms，随常规测试一起运行。

防突发抖动策略：
  - 10 轮 warmup（预热 JIT / 连接池 / 缓存）
  - 10 轮正式测量，排序后去掉最慢的 1 个样本
  - 断言使用 **median**（非 mean），天然抗单次 GC/调度抖动

每次运行的结果自动保存到 tests/routers/.benchmark_results.json。

    uv run pytest -v                                          # 包含基准测试
    uv run pytest tests/routers/test_benchmark_checkin.py -v  # 单独运行

测试场景：
  - 5  账号并发签到
  - 10 账号并发签到
  - 20 账号并发签到
  - 50 账号并发签到

每次测量路线：
  test_execute_checkin_latency    SessionPool.execute_checkin 纯逻辑
  test_endpoint_to_api_latency    HTTP 端点 → DI → DB → SessionPool → mock API
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

# ── 计时参数 ──

WARMUP = 10          # 预热轮数（不计入结果）
ITERATIONS = 10      # 正式测量轮数
DROPS = 1            # 去掉最多 DROPS 个最慢样本

# ── Helpers ──


def _create_accounts(
    db_engine,
    user_id: int,
    course_id: str,
    count: int,
    *,
    start_id: int = 10000,
) -> list[int]:
    """在测试数据库中创建 *count* 个账号并绑定课程，返回 account_id 列表。"""
    from app.core.security import encrypt_credential
    from app.models import Account, Course, CourseBinding, UserAccount
    from sqlmodel import Session as SMSession

    with SMSession(db_engine) as db:
        course = db.get(Course, course_id)
        if course is None:
            course = Course(
                id=course_id, code=f"CODE-{course_id}",
                course_name="测试课程", semester="2026-2027", term="1",
            )
            db.add(course)
            db.flush()

        ids: list[int] = []
        for i in range(count):
            aid = start_id + i
            acct = Account(
                id=aid,
                email=f"benchmark-{aid}@test.com",
                password=encrypt_credential("mock_password"),
                uid=str(aid),
                username=f"User-{i}",
            )
            db.add(acct)
            db.add(UserAccount(user_id=user_id, account_id=aid))
            db.add(CourseBinding(course_id=course_id, account_id=aid, is_active=True))
            ids.append(aid)

        db.commit()
        return ids


def _measure(
    fn,
    *,
    warmup: int = WARMUP,
    iterations: int = ITERATIONS,
    drops: int = DROPS,
) -> list[float]:
    """运行 *fn* 并返回测量到的延迟（秒），已去掉最慢的 *drops* 个样本。"""
    # Warmup — 建立连接池、加载 JIT 等
    for _ in range(warmup):
        fn()

    # 正式测量
    raw: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        elapsed = time.perf_counter() - start
        raw.append(elapsed)

    # 排序并截断最慢的 outliers
    raw.sort()
    trimmed = raw[: len(raw) - drops] if drops > 0 else raw
    return trimmed


def _report(
    label: str,
    accounts: int,
    times: list[float],
) -> dict:
    """打印并返回延迟统计摘要。"""
    avg = statistics.mean(times) * 1000
    median = statistics.median(times) * 1000
    _min = times[0] * 1000
    _max = times[-1] * 1000
    p90 = times[int(len(times) * 0.9)] * 1000

    print(
        f"\n  [{label}]  {accounts:>2} accounts  "
        f"median={median:.1f}  avg={avg:.1f}  p90={p90:.1f}  "
        f"min={_min:.1f}  max={_max:.1f}  (n={len(times)})"
    )

    return {
        "accounts": accounts,
        "median_ms": round(median, 2),
        "avg_ms": round(avg, 2),
        "p90_ms": round(p90, 2),
        "min_ms": round(_min, 2),
        "max_ms": round(_max, 2),
        "samples": len(times),
    }


# ── Mock all KetangPaiAPI network calls ──


@pytest.fixture(autouse=True)
def _mock_ketangpai_api(monkeypatch: pytest.MonkeyPatch):
    """替换 KetangPaiAPI 使其所有网络调用立即返回 mock 结果。"""
    from app.core import api as api_module

    # init — 不创建真实 httpx 客户端
    def _mock_init(self, email, password, token=None):
        self.email = email
        self.password = password
        self.token = token or "mock-token"
        self.user_info = None

    monkeypatch.setattr(api_module.KetangPaiAPI, "__init__", _mock_init)

    # qr_check_in / gps_check_in — 立即成功
    async def _mock_checkin(self, data, client_ip=""):
        return api_module.CheckInResult(
            email=self.email, success=True, message="签到成功(mock)", code=0,
        )

    monkeypatch.setattr(api_module.KetangPaiAPI, "qr_check_in", _mock_checkin)
    monkeypatch.setattr(api_module.KetangPaiAPI, "gps_check_in", _mock_checkin)

    # login — 不实际请求
    async def _mock_login(self):
        self.token = "mock-token"
        return api_module.LoginResponse(
            status=1, code=200, message="ok",
            data=api_module.LoginData(token="mock-token", uid="mock-uid"),
        )

    monkeypatch.setattr(api_module.KetangPaiAPI, "login", _mock_login)

    # get_user_info — 最小数据
    async def _mock_user_info(self):
        return api_module.GetUserInfoResponse(
            status=1, code=200, message="ok",
            data=api_module.UserInfo(
                id="mock-id", username="Mock User", avatar="",
                usertype="1", stno="STNO", school="School",
                account=self.email, mobile="",
                notify1="0", notify2="0", notify3="0", notify4="0",
                isenterprise="0", atteststate=0, attestInfo=[],
                isvip=0, openid="", unionid="", wechat_nikename="",
                teachcourse=[], majors=[], majorsV2=[],
                additionInfo=api_module.AdditionInfo(),
                userScore=0, coid=0, endtime=0, i18nSwitchEnabled=0,
            ),
        )

    monkeypatch.setattr(api_module.KetangPaiAPI, "get_user_info", _mock_user_info)
    monkeypatch.setattr(api_module.KetangPaiAPI, "get_course_list", AsyncMock(return_value=[]))
    monkeypatch.setattr(api_module.KetangPaiAPI, "close", AsyncMock())
    monkeypatch.setattr(api_module.KetangPaiAPI, "get_not_finish_attence_student", AsyncMock(return_value=[]))
    monkeypatch.setattr(api_module.KetangPaiAPI, "get_digit_attence", AsyncMock(return_value=""))
    monkeypatch.setattr(api_module.KetangPaiAPI, "get_attence_building_gps", AsyncMock(return_value={}))
    monkeypatch.setattr(api_module.KetangPaiAPI, "get_attence_location", AsyncMock(return_value={}))

    yield


# ── Mock SessionPool.create — 直接填充 clients，跳过真实登录 ──


@pytest.fixture(autouse=True)
def _mock_session_pool(monkeypatch: pytest.MonkeyPatch):
    """让 session_pool.create / ensure_client 不实际登录课堂派。"""
    from app.core.sessions import session_pool
    from app.core.api import KetangPaiAPI

    async def _mock_create(accounts, update_status=True):
        for account in accounts:
            if account.id not in session_pool.clients:
                client = KetangPaiAPI(account.email, "mock", "mock-token")
                session_pool.clients[account.id] = (client, time.time())
        return True

    async def _mock_ensure_client(account_id, db_session=None):
        entry = session_pool.clients.get(account_id)
        return entry[0] if entry else None

    monkeypatch.setattr(session_pool, "create", _mock_create)
    monkeypatch.setattr(session_pool, "ensure_client", _mock_ensure_client)

    yield

    session_pool.clients.clear()


# ── 基准测试主体 ──


COURSE_ID = "bench-course-001"
QR_BODY = {
    "ticketid": "bench-ticket-001",
    "expire": int(time.time()) + 7200,
    "sign": "bench-sign",
    "courseid": COURSE_ID,
}


@pytest.mark.parametrize("num_accounts", [5, 10, 20, 50])
class TestCheckinBenchmark:
    """签到链路延迟基准测试。"""

    @pytest.fixture(autouse=True)
    def _setup_accounts(self, db_engine, num_accounts):
        """创建 N 个测试账号并注入 SessionPool。"""
        self.account_ids = _create_accounts(
            db_engine,
            user_id=1,
            course_id=COURSE_ID,
            count=num_accounts,
        )
        from app.core.sessions import session_pool
        from app.core.api import KetangPaiAPI

        session_pool.clients.clear()
        for aid in self.account_ids:
            client = KetangPaiAPI(f"bench-{aid}@test.com", "mock", "mock-token")
            session_pool.clients[aid] = (client, time.time())

        yield
        session_pool.clients.clear()

    # ── 测试 1：纯逻辑延迟 ──

    def test_execute_checkin_latency(self, num_accounts):
        """SessionPool.execute_checkin 纯逻辑延迟（不含 DB 查询和 FastAPI DI）。"""
        from app.core.api import QRCheckInRequest
        from app.core.sessions import session_pool
        import asyncio

        req = QRCheckInRequest(**QR_BODY)

        def _run():
            async def _inner():
                return await session_pool.execute_checkin(
                    user_id=1,
                    account_ids=self.account_ids,
                    data=req,
                    client_ip="",
                )
            return asyncio.run(_inner())

        times = _measure(_run)
        stats = _report("execute_checkin", num_accounts, times)

        # 记录到 conftest 收集器
        from tests.conftest import BENCHMARK_RESULTS
        BENCHMARK_RESULTS.append({
            "test": "execute_checkin",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **stats,
        })

        # median < 50ms 是硬性要求（对偶发 GC/调度抖动天然鲁棒）
        assert stats["median_ms"] < 50.0, (
            f"execute_checkin median latency {stats['median_ms']:.1f}ms "
            f"exceeds 50ms limit for {num_accounts} accounts "
            f"(avg={stats['avg_ms']:.1f}ms, p90={stats['p90_ms']:.1f}ms, "
            f"n={stats['samples']})"
        )

    # ── 测试 2：全链路 HTTP 延迟 ──

    def test_endpoint_to_api_latency(self, client, db_engine, num_accounts):
        """HTTP 端点 → FastAPI DI → DB → SessionPool → mock API 全链路延迟。"""
        import json as j
        import random

        # 注册测试用户
        suffix = random.randint(100000, 999999)
        email = f"bench-ep-{suffix}@test.com"
        reg_resp = client.post(
            "/api/register",
            json={"email": email, "password": "BenchPass1"},
        )
        assert reg_resp.status_code == 200
        token = reg_resp.json()["data"]["access_token"]

        # 将账号关联到这个新用户
        from app.models import UserAccount
        from sqlmodel import Session as SMSession

        with SMSession(db_engine) as db:
            for aid in self.account_ids:
                db.add(UserAccount(user_id=2, account_id=aid))
            db.commit()

        body = j.dumps(QR_BODY)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

        def _run():
            resp = client.post("/api/checkin", content=body, headers=headers)
            assert resp.status_code == 200, f"Checkin failed: {resp.text}"

        times = _measure(_run)
        stats = _report("HTTP endpoint", num_accounts, times)

        # 记录到 conftest 收集器
        from tests.conftest import BENCHMARK_RESULTS
        BENCHMARK_RESULTS.append({
            "test": "http_endpoint",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **stats,
        })

        # median < 50ms 是硬性要求
        assert stats["median_ms"] < 50.0, (
            f"HTTP endpoint median latency {stats['median_ms']:.1f}ms "
            f"exceeds 50ms limit for {num_accounts} accounts "
            f"(avg={stats['avg_ms']:.1f}ms, p90={stats['p90_ms']:.1f}ms, "
            f"n={stats['samples']})"
        )
