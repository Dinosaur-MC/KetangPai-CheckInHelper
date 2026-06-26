"""签到链路延迟基准测试。

测量从 API 端点入口到首次请求课堂派 API 前的框架+业务逻辑延迟。
默认被 pytest 跳过（mark: benchmark），需显式运行：

    uv run pytest -m benchmark -v
    uv run pytest tests/routers/test_benchmark_checkin.py -v --tb=short

测试场景（各 5 轮取均值）：
  - 5  账号并发签到
  - 10 账号并发签到
  - 20 账号并发签到
  - 50 账号并发签到

每次测量路线：
  test_execute_checkin_latency    SessionPool.execute_checkin 纯逻辑
  test_endpoint_to_api_latency    HTTP 端点 → DI → DB → SessionPool → mock API
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.benchmark

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

        times = []
        for _ in range(5):
            start = time.perf_counter()

            async def _run():
                return await session_pool.execute_checkin(
                    user_id=1,
                    account_ids=self.account_ids,
                    data=req,
                    client_ip="",
                )

            asyncio.run(_run())
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        avg = sum(times) / len(times)
        _min_val = min(times)
        _max_val = max(times)

        print(
            f"\n  [execute_checkin]  {num_accounts:>2} accounts (5 runs):  "
            f"avg={avg*1000:.1f} ms   min={_min_val*1000:.1f} ms   max={_max_val*1000:.1f} ms"
        )

        # 逻辑链路延迟必须控制在 50ms 内
        assert avg < 0.050, (
            f"execute_checkin avg latency {avg*1000:.1f}ms exceeds 50ms limit "
            f"for {num_accounts} accounts"
        )

    # ── 测试 2：全链路 HTTP 延迟 ──

    def test_endpoint_to_api_latency(self, client, db_engine, num_accounts):
        """HTTP 端点 → FastAPI DI → DB → SessionPool → mock API 全链路延迟。"""
        import json as j

        # 注册测试用户并获取 token
        import random
        suffix = random.randint(100000, 999999)
        email = f"bench-ep-{suffix}@test.com"

        # 先注册
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

        # Warmup
        client.post(
            "/api/checkin",
            content=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )

        times = []
        for _ in range(5):
            start = time.perf_counter()
            resp = client.post(
                "/api/checkin",
                content=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            )
            elapsed = time.perf_counter() - start
            assert resp.status_code == 200, f"Checkin failed: {resp.text}"
            times.append(elapsed)

        avg = sum(times) / len(times)
        _min_val = min(times)
        _max_val = max(times)

        print(
            f"\n  [HTTP endpoint]  {num_accounts:>2} accounts (5 runs):  "
            f"avg={avg*1000:.1f} ms   min={_min_val*1000:.1f} ms   max={_max_val*1000:.1f} ms"
        )

        # 逻辑链路延迟必须控制在 50ms 内（含 HTTP 框架开销）
        assert avg < 0.050, (
            f"HTTP endpoint avg latency {avg*1000:.1f}ms exceeds 50ms limit "
            f"for {num_accounts} accounts"
        )
