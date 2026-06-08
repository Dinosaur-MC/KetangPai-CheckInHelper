import asyncio
import time
from threading import Lock
from app.api import CheckInRequest, KetangPaiAPI, CheckInResult
from app.db import get_session, get_redis_client
from app.models import Account, CheckInLog

import logging

logger = logging.getLogger(__name__)

TOKEN_EXPIRE_TIME = 60 * 60 * 24 * 5  # Redis 缓存 token 的 TTL
SESSION_TTL = 30 * 60  # 内存会话过期时间 30 分钟
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
        # clients: {account_id: (KetangPaiAPI, last_used_timestamp)}
        self.clients: dict[int, tuple[KetangPaiAPI, float]] = {}
        self.lock = Lock()
        self.exec_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKINS)
        logger.info("会话池初始化完成")

    # ------------------------------------------------------------------
    # 会话过期清理
    # ------------------------------------------------------------------

    def _cleanup_expired(self):
        """移除过期的会话（调用方需持有 self.lock）。"""
        now = time.time()
        expired = [
            aid for aid, (_, ts) in self.clients.items()
            if now - ts > SESSION_TTL
        ]
        for aid in expired:
            try:
                self.clients.pop(aid)[0].close()
            except Exception:
                pass
            logger.debug("Session expired for account %s", aid)

    def _touch(self, account_id: int):
        """刷新会话最后使用时间。"""
        entry = self.clients.get(account_id)
        if entry is not None:
            self.clients[account_id] = (entry[0], time.time())

    # ------------------------------------------------------------------
    # 同步方法：create / remove（通过 threading.Lock 保护）
    # ------------------------------------------------------------------

    def create(
        self, accounts: list[Account], update_status: bool = True
    ) -> bool:
        """为 *accounts* 创建登录会话，已存在的跳过。

        :param update_status: 是否数据库更新账号状态（login 成功=1，失败=-1）。
            新建账号验证时应设为 False，因为账号尚未入库。
        :return: True 表示所有会话均就绪；False 表示有账号登录失败。
        """
        with self.lock:
            self._cleanup_expired()
            r = get_redis_client()
            vacc = [x for x in accounts if x.id not in self.clients]
            if len(vacc) == 0:
                return True
            all_ok = True
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
                        if update_status:
                            self._set_account_status(account.id, 1)
                    except Exception as e:
                        logger.warning(
                            "Failed to login account %s: %s", account.email, e
                        )
                        if update_status:
                            self._set_account_status(account.id, -1)
                        all_ok = False
                        continue
                self.clients[account.id] = (client, time.time())
            return all_ok

    def remove(self, account_ids: list[int]) -> bool:
        """关闭并移除指定账号的会话。"""
        with self.lock:
            for x in account_ids:
                entry = self.clients.pop(x, None)
                if entry is not None:
                    try:
                        entry[0].close()
                    except Exception:
                        pass
            return True

    def get_user_info(self, account_id: int) -> dict | None:
        """获取指定账号的用户信息，返回 dict 或 None。

        如果会话不存在则新建并登录（调用方需确保 Account 密码正确）。
        会话过期会被重建。
        """
        with self.lock:
            self._cleanup_expired()
            entry = self.clients.get(account_id)

        if entry is not None:
            self._touch(account_id)
            try:
                resp = entry[0].get_user_info()
                return resp.data.model_dump()
            except Exception as e:
                logger.warning(
                    "get_user_info failed for account %s: %s", account_id, e
                )
                # 会话可能失效，移除旧客户端并尝试重建
                with self.lock:
                    old = self.clients.pop(account_id, None)
                    if old is not None:
                        try:
                            old[0].close()
                        except Exception:
                            pass
                # fall through to rebuild
                entry = None

        # 无缓存或缓存失效 — 从 DB 重建
        try:
            with get_session() as db:
                account = db.get(Account, account_id)
            if account is None:
                return None

            r = get_redis_client()
            try:
                token = r.get(f"account:{account_id}:token")
            except Exception:
                token = None

            client = KetangPaiAPI(account.email, account.password, token)
            if not token:
                client.login()
                try:
                    r.set(
                        f"account:{account_id}:token",
                        client.token,
                        TOKEN_EXPIRE_TIME,
                    )
                except Exception:
                    pass
                self._set_account_status(account_id, 1)

            with self.lock:
                self.clients[account_id] = (client, time.time())

            resp = client.get_user_info()
            return resp.data.model_dump()

        except Exception as e:
            logger.error(
                "get_user_info failed for account %s: %s", account_id, e
            )
            return None

    def _set_account_status(self, account_id: int, status: int):
        """更新账号状态字段。"""
        try:
            with get_session() as db:
                acct = db.get(Account, account_id)
                if acct is not None and acct.status != status:
                    acct.status = status
                    db.add(acct)
                    db.commit()
                    logger.info(
                        "Account %s status updated to %s", account_id, status
                    )
        except Exception as e:
            logger.error(
                "Failed to update account %s status: %s", account_id, e
            )

    # ------------------------------------------------------------------
    # 异步方法：execute_checkin（通过 asyncio.Lock 序列化批次）
    # ------------------------------------------------------------------

    async def execute_checkin(
        self, user_id: int, account_ids: list[int], data: CheckInRequest
    ) -> dict[int, CheckInResult | None]:
        """批量签到入口。"""
        # ── 快照阶段 ──
        with self.lock:
            self._cleanup_expired()
            snapshot = {aid: self.clients.get(aid) for aid in account_ids}

        # ── 执行阶段 ──
        async with self.exec_lock:
            results: dict[int, CheckInResult | None] = {}
            with get_session() as db:
                r = get_redis_client()
                valid = r.get(
                    f"checkin:{data.courseid}:invalid:{data.ticketid}"
                )
                if not valid:
                    return {aid: None for aid in account_ids}

                # ----- Canary -----
                first_aid: int | None = None
                first_client: KetangPaiAPI | None = None
                first_idx = -1
                for i, aid in enumerate(account_ids):
                    entry = snapshot.get(aid)
                    if entry is not None:
                        first_aid = aid
                        first_client = entry[0]
                        first_idx = i
                        break

                if first_client is None:
                    return {aid: None for aid in account_ids}

                try:
                    first_result = await asyncio.to_thread(
                        first_client.check_in, data
                    )
                except Exception as e:
                    first_result = CheckInResult(
                        email=first_client.email,
                        success=False,
                        message=f"签到失败：{e}",
                    )

                self._record(
                    db, r, user_id, first_aid, data.courseid,
                    first_client, first_result,
                )
                self._touch(first_aid)
                results[first_aid] = first_result

                if not first_result.success:
                    if (
                        first_result.message == "二维码已过期"
                        or first_result.message == "考勤已结束"
                    ):
                        r.set(
                            f"checkin:{data.courseid}:invalid:{data.ticketid}",
                            "1", 3600,
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
                remaining = account_ids[first_idx + 1:]
                if remaining:
                    tasks = [
                        asyncio.create_task(
                            self._checkin_one(
                                snapshot, db, r, user_id, aid, data,
                            )
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
        snapshot: dict[int, tuple[KetangPaiAPI, float] | None],
        db, r,
        user_id: int, account_id: int, data: CheckInRequest,
    ) -> tuple[int, CheckInResult | None]:
        """签到单个账号，受 semaphore 限流。"""
        async with self.semaphore:
            entry = snapshot.get(account_id)
            if entry is None:
                return (account_id, None)
            client = entry[0]
            try:
                result = await asyncio.to_thread(client.check_in, data)
            except Exception as e:
                logger.error(
                    "Check-in failed for account %s (id=%s): %s",
                    client.email, account_id, e,
                )
                return (
                    account_id,
                    CheckInResult(
                        email=client.email,
                        success=False,
                        message=f"签到失败：{e}",
                    ),
                )
            self._touch(account_id)
            self._record(
                db, r, user_id, account_id, data.courseid, client, result,
            )
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
