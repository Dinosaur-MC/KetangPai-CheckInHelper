import asyncio
from threading import Lock
from app.api import CheckInRequest, KetangPaiAPI, CheckInResult
from app.db import get_session, get_redis_client
from app.models import Account, CheckInLog

import logging

logger = logging.getLogger(__name__)

TOKEN_EXPIRE_TIME = 60 * 60 * 24 * 5
MAX_CONCURRENT_CHECKINS = 5


class SessionPool:
    """会话池：管理多账号的登录态，支持批量签到。

    并发模型：
    - ``self.lock``（threading.Lock）：保护 ``self.clients`` 字典的读写，
      create / remove / execute_checkin 的快照阶段都会持有它。
    - ``self.exec_lock``（asyncio.Lock）：序列化签到批次的执行阶段，
      保证同一时刻只有一个批次在跑签到（不同批次之间不交错）。
    - ``self.semaphore``（asyncio.Semaphore）：限制同一批次内对第三方
      API 的并发请求数，避免触发远端限流。
    """

    def __init__(self):
        self.clients: dict[int, KetangPaiAPI] = {}
        self.lock = Lock()
        self.exec_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKINS)
        logger.info("会话池初始化完成")

    # ------------------------------------------------------------------
    # 同步方法：create / remove（线程安全，通过 threading.Lock 保护）
    # ------------------------------------------------------------------

    def create(self, accounts: list[Account]) -> bool:
        """为 *accounts* 创建登录会话，已存在的跳过。"""
        with self.lock:
            r = get_redis_client()
            vacc = [x for x in accounts if x.id not in self.clients]
            if len(vacc) == 0:
                return False
            for account in vacc:
                if account is None:
                    continue
                try:
                    token = r.get(f"account:{account.id}:token")
                except Exception:
                    token = None
                client = KetangPaiAPI(account.email, account.password, token)
                if not token:
                    try:
                        client.login()
                        try:
                            r.set(
                                f"account:{account.id}:token",
                                client.token,
                                TOKEN_EXPIRE_TIME,
                            )
                        except Exception:
                            pass  # Redis 写入失败非致命
                    except Exception as e:
                        logger.warning(
                            "Failed to login account %s: %s", account.email, e
                        )
                        continue
                self.clients[account.id] = client
            return True

    def remove(self, account_ids: list[int]) -> bool:
        """关闭并移除指定账号的会话。"""
        with self.lock:
            for x in account_ids:
                if x in self.clients:
                    try:
                        self.clients.pop(x).close()
                    except Exception:
                        pass
            return True

    # ------------------------------------------------------------------
    # 异步方法：execute_checkin（通过 asyncio.Lock 序列化批次）
    # ------------------------------------------------------------------

    async def execute_checkin(
        self, user_id: int, account_ids: list[int], data: CheckInRequest
    ) -> dict[int, CheckInResult | None]:
        """批量签到入口。

        流程：
        1. 从 ``self.clients`` 快照出需要的客户端（线程安全）。
        2. **Canary 检测**：用第一个有客户端的账号先签到一次。
           - 若失败（例如二维码过期），整个批次直接返回，后续账号跳过。
           - 若成功，并发处理剩余账号。
        3. 并发阶段受 ``self.semaphore`` 限流，避免同时大量请求第三方。
        """
        # ── 快照阶段 ──
        # 在 threading.Lock 下取出本次需要的客户端引用，之后不再访问
        # self.clients，避免与 create/remove 产生读写竞争。
        with self.lock:
            snapshot = {aid: self.clients.get(aid) for aid in account_ids}

        # ── 执行阶段 ──
        # asyncio.Lock 保证批次间串行，但内部可以并发。
        async with self.exec_lock:
            results: dict[int, CheckInResult | None] = {}
            with get_session() as db:
                r = get_redis_client()
                valid = r.get(f"checkin:{data.courseid}:invalid:{data.ticketid}")
                if not valid:
                    return {aid: None for aid in account_ids}

                # ----- Canary：先试第一个 -----
                first_aid: int | None = None
                first_client: KetangPaiAPI | None = None
                first_idx = -1
                for i, aid in enumerate(account_ids):
                    c = snapshot.get(aid)
                    if c is not None:
                        first_aid = aid
                        first_client = c
                        first_idx = i
                        break

                if first_client is None:
                    # 没有任何可用客户端
                    return {aid: None for aid in account_ids}

                try:
                    first_result = await asyncio.to_thread(first_client.check_in, data)
                except Exception as e:
                    first_result = CheckInResult(
                        email=first_client.email,
                        success=False,
                        message=f"签到失败：{e}",
                    )

                self._record(
                    db, r, user_id, first_aid, data.courseid, first_client, first_result
                )
                results[first_aid] = first_result

                if not first_result.success:
                    # 通常是二维码过期/考勤结束等全局性失败，
                    # 后续账号结果一定相同，跳过以节省请求。
                    if (
                        first_result.message == "二维码已过期"
                        or first_result.message == "考勤已结束"
                    ):
                        r.set(
                            f"checkin:{data.courseid}:invalid:{data.ticketid}",
                            "1",
                            3600,
                        )
                    try:
                        db.commit()
                    except Exception as e:
                        logger.error("Failed to commit: %s", e)
                        db.rollback()
                    for i, aid in enumerate(account_ids):
                        if i == first_idx:
                            continue
                        results[aid] = None
                    return results

                # ----- 并发处理剩余账号 -----
                remaining = account_ids[first_idx + 1 :]
                if remaining:
                    tasks = [
                        asyncio.create_task(
                            self._checkin_one(snapshot, db, r, user_id, aid, data)
                        )
                        for aid in remaining
                    ]
                    remaining_results = await asyncio.gather(*tasks)
                    results.update(remaining_results)

                try:
                    db.commit()
                except Exception as e:
                    logger.error("Failed to commit: %s", e)
                    db.rollback()

            return results

    async def _checkin_one(
        self,
        snapshot: dict[int, KetangPaiAPI | None],
        db,
        r,
        user_id: int,
        account_id: int,
        data: CheckInRequest,
    ) -> tuple[int, CheckInResult | None]:
        """签到单个账号，受 semaphore 限流。"""
        async with self.semaphore:
            client = snapshot.get(account_id)
            if client is None:
                return (account_id, None)
            try:
                result = await asyncio.to_thread(client.check_in, data)
            except Exception as e:
                logger.error(
                    "Check-in failed for account %s (id=%s): %s",
                    client.email,
                    account_id,
                    e,
                )
                return (
                    account_id,
                    CheckInResult(
                        email=client.email,
                        success=False,
                        message=f"签到失败：{e}",
                    ),
                )
            self._record(db, r, user_id, account_id, data.courseid, client, result)
            return (account_id, result)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _record(self, db, r, user_id, account_id, course_id, client, result):
        """记录签到日志并刷新 token 缓存（失败不抛异常）。"""
        try:
            db.add(
                CheckInLog(
                    user_id=user_id,
                    account_id=account_id,
                    course_id=course_id,
                    status=1 if result.success else 0,
                )
            )
        except Exception as e:
            logger.error("Failed to write CheckInLog: %s", e)
        try:
            r.set(
                f"account:{account_id}:token",
                client.token,
                TOKEN_EXPIRE_TIME,
            )
        except Exception:
            pass


# 创建会话池（模块级单例）
session_pool = SessionPool()
