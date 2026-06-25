import asyncio
import time
from threading import Lock

from sqlmodel import Session
from app.core.api import QRCheckInRequest, CheckInRequest, KetangPaiAPI, CheckInResult, is_position_error, _extract_gps
from app.core.db import get_session, get_redis_client
from app.models import Account, CheckInLog
from app.core.security import decrypt_credential

import logging

logger = logging.getLogger(__name__)

TOKEN_EXPIRE_TIME = 60 * 60 * 24 * 5  # Redis 缓存 token 的 TTL
SESSION_TTL = 30 * 60  # 内存会话过期时间 30 分钟
MAX_CONCURRENT_CHECKINS = 5
GPS_COORDS_CACHE_TTL = 60 * 20  # 建筑 GPS 坐标缓存 20 分钟


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
        expired_ids = [
            aid for aid, (_, ts) in list(self.clients.items()) if now - ts > SESSION_TTL
        ]
        for aid in expired_ids:
            try:
                self.clients.pop(aid)[0].close()
            except Exception:
                pass
            logger.debug("Session expired for account %s", aid)

    def _touch(self, account_id: int):
        """刷新会话最后使用时间（调用方需持有 self.lock）。"""
        entry = self.clients.get(account_id)
        if entry is not None:
            self.clients[account_id] = (entry[0], time.time())

    # ------------------------------------------------------------------
    # 同步方法：create / remove（通过 threading.Lock 保护）
    # ------------------------------------------------------------------

    def create(self, accounts: list[Account], update_status: bool = True) -> bool:
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
                client = KetangPaiAPI(
                    account.email, decrypt_credential(account.password), token
                )
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
                        msg = str(e) or "登录失败"
                        logger.warning(
                            "Failed to login account %s: %s",
                            account.email[:5] + "..." + account.email[-5:],
                            msg,
                        )
                        if update_status:
                            self._set_account_status(account.id, -1, msg)
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

    def _ensure_client(
        self,
        account_id: int,
        db_session: Session | None = None,
    ) -> KetangPaiAPI | None:
        """确保 *account_id* 在 self.clients 中有可用会话。

        调用方建议持有 self.lock；为安全，本方法内部自行加锁。
        返回客户端或 None（账号不存在或登录失败）。

        :param db_session: 可选的现有 DB 会话，避免在事务外创建独立连接。
        """
        with self.lock:
            self._cleanup_expired()
            entry = self.clients.get(account_id)

        if entry is not None:
            with self.lock:
                self._touch(account_id)
            return entry[0]

        # 从 DB 重建
        try:
            if db_session is not None:
                account = db_session.get(Account, account_id)
            else:
                with get_session() as db:
                    account = db.get(Account, account_id)
            if account is None:
                return None

            r = get_redis_client()
            try:
                token = r.get(f"account:{account_id}:token")
            except Exception:
                token = None

            client = KetangPaiAPI(
                account.email, decrypt_credential(account.password), token
            )
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
                self._set_account_status(
                    account_id,
                    1,
                    db_session=db_session,
                )

            with self.lock:
                self.clients[account_id] = (client, time.time())
            return client

        except Exception as e:
            logger.error("Failed to ensure client for account %s: %s", account_id, e)
            return None

    # ------------------------------------------------------------------
    # 查询方法（均支持批量：int → result, list[int] → dict[int, result]）
    # ------------------------------------------------------------------

    def get_account_info(self, account_ids: int | list[int]) -> dict | None:
        """获取账号用户信息。

        :param account_ids: 单个 ID 或 ID 列表。
        :return: 单个 ID → dict 或 None；列表 → {id: dict | None, ...}。
        """
        single = isinstance(account_ids, int)
        ids = [account_ids] if single else account_ids
        result: dict[int, dict | None] = {}

        for aid in ids:
            client = self._ensure_client(aid)
            if client is None:
                result[aid] = None
                continue
            try:
                resp = client.get_user_info()
                result[aid] = resp.data.model_dump()
            except Exception as e:
                logger.warning("get_user_info failed for account %s: %s", aid, e)
                # 移除失效会话以便下次重建
                with self.lock:
                    old = self.clients.pop(aid, None)
                    if old is not None:
                        try:
                            old[0].close()
                        except Exception:
                            pass
                result[aid] = None

        return result[account_ids] if single else result

    def get_course_list(
        self, account_ids: int | list[int]
    ) -> list[dict] | None | dict[int, list[dict] | None]:
        """获取账号的学期课程列表。

        :param account_ids: 单个 ID 或 ID 列表。
        :return: 单个 → list[dict] 或 None；列表 → {id: list[dict] | None, ...}。
        """
        single = isinstance(account_ids, int)
        ids = [account_ids] if single else account_ids
        result: dict[int, list[dict] | None] = {}

        for aid in ids:
            client = self._ensure_client(aid)
            if client is None:
                result[aid] = None
                continue
            try:
                items = client.get_course_list()
                result[aid] = [item.model_dump() for item in items]
            except Exception as e:
                logger.warning("get_course_list failed for account %s: %s", aid, e)
                with self.lock:
                    old = self.clients.pop(aid, None)
                    if old is not None:
                        try:
                            old[0].close()
                        except Exception:
                            pass
                result[aid] = None

        return result[account_ids] if single else result

    def _set_account_status(
        self,
        account_id: int,
        status: int,
        message: str = "",
        db_session: Session | None = None,
    ):
        """更新账号状态字段，可选附带状态说明。

        :param db_session: 可选的现有 DB 会话。传入时复用该会话，
            避免在已有事务上下文外创建独立连接。
        """
        if db_session is not None:
            try:
                acct = db_session.get(Account, account_id)
                if acct is not None and (
                    acct.status != status or acct.status_message != message
                ):
                    acct.status = status
                    acct.status_message = message
                    db_session.add(acct)
                    logger.info(
                        "Account %s status updated to %s: %s",
                        account_id,
                        status,
                        message,
                    )
            except Exception as e:
                logger.error("Failed to update account %s status: %s", account_id, e)
            return
        try:
            with get_session() as db:
                acct = db.get(Account, account_id)
                if acct is not None and (
                    acct.status != status or acct.status_message != message
                ):
                    acct.status = status
                    acct.status_message = message
                    db.add(acct)
                    db.commit()
                    logger.info(
                        "Account %s status updated to %s: %s",
                        account_id,
                        status,
                        message,
                    )
        except Exception as e:
            logger.error("Failed to update account %s status: %s", account_id, e)

    # ------------------------------------------------------------------
    # 异步方法：execute_checkin（通过 asyncio.Lock 序列化批次）
    # ------------------------------------------------------------------

    async def execute_checkin(
        self,
        user_id: int,
        account_ids: list[int],
        data: QRCheckInRequest,
        client_ip: str = "",
    ) -> dict[int, CheckInResult | None]:
        """批量二维码签到入口。

        保证每个 account_id 都返回有意义的 CheckInResult（非 None），
        即使账号无可用会话也会尝试按需创建。

        :param client_ip: 客户端真实 IP，透传至课堂派签到请求。
        """
        logger.info(
            "Starting check-in for user=%s course=%s ticket=%s accounts=%s",
            user_id,
            data.courseid,
            data.ticketid,
            account_ids,
        )

        # ── 快照阶段 ──
        with self.lock:
            self._cleanup_expired()
            snapshot = {aid: self.clients.get(aid) for aid in account_ids}

        # ── 执行阶段 ──
        async with self.exec_lock:
            results: dict[int, CheckInResult | None] = {}
            with get_session() as db:
                r = get_redis_client()

                # 检查缓存的「该 ticket 全局无效」标记
                invalid_key = f"checkin:{data.courseid}:invalid:{data.ticketid}"
                if r and r.get(invalid_key):
                    logger.info(
                        "Ticket %s for course %s is cached as invalid — "
                        "skipping all accounts",
                        data.ticketid,
                        data.courseid,
                    )
                    # 为每个账号生成有意义的"跳过"结果
                    cached = self._build_skip_results(
                        account_ids, "已跳过（该签到二维码已失效）"
                    )
                    for aid, cr in cached.items():
                        self._record(
                            db,
                            r,
                            user_id,
                            aid,
                            data.courseid,
                            cr.email,
                            cr,
                        )
                    try:
                        db.commit()
                    except Exception as e:
                        logger.error("Failed to commit: %s", e)
                        db.rollback()
                    return cached

                # ----- Canary：先试第一个可用账号 -----
                canary = self._pick_canary(snapshot, account_ids)
                if canary is None:
                    logger.warning(
                        "No session available for any account in %s",
                        account_ids,
                    )
                    # 尝试为每个无会话的账号按需创建并执行
                    return await self._checkin_all_ensure(
                        snapshot,
                        db,
                        r,
                        user_id,
                        account_ids,
                        data,
                        client_ip=client_ip,
                    )

                first_aid, first_client = canary

                logger.debug(
                    "Canary check-in: account %s (%s)",
                    first_aid,
                    first_client.email[:5] + "..." + first_client.email[-5:],
                )
                # canary 也检查去重
                dedup_key = f"checkin_done:{data.ticketid}:{first_aid}"
                try:
                    if r and r.get(dedup_key):
                        first_result = CheckInResult(
                            email=first_client.email,
                            success=True,
                            message="已签到（跳过重复调用）",
                        )
                    else:
                        first_result = await asyncio.to_thread(
                            first_client.qr_check_in, data, client_ip
                        )
                except Exception as e:
                    logger.warning(
                        "Canary check-in exception for account %s: %s",
                        first_aid,
                        e,
                    )
                    first_result = CheckInResult(
                        email=first_client.email,
                        success=False,
                        message=f"签到失败：{e}",
                    )

                # canary 成功后写入去重标记
                if first_result.success:
                    try:
                        ttl = max(data.expire - int(time.time()), 300)
                        r.set(dedup_key, "1", ttl)
                    except Exception:
                        pass

                self._record(
                    db,
                    r,
                    user_id,
                    first_aid,
                    data.courseid,
                    first_client,
                    first_result,
                )
                self._touch(first_aid)
                results[first_aid] = first_result

                logger.info(
                    "Canary result for account %s: success=%s message=%s",
                    first_aid,
                    first_result.success,
                    first_result.message,
                )

                if not first_result.success:
                    # 缓存全局性失败标记（code 30319/30322 表明整批次均不可用）
                    if first_result.code in (30319, 30322):
                        if r:
                            r.set(invalid_key, "1", 3600)
                        logger.info(
                            "Ticket %s marked as globally invalid "
                            "(code=%s reason=%s)",
                            data.ticketid,
                            first_result.code,
                            first_result.message,
                        )

                    # 其余账号标记为"跳过"，但仍写入日志
                    for aid in account_ids:
                        if aid == first_aid:
                            continue
                        skip_email = self._resolve_client_email(
                            snapshot,
                            aid,
                        )
                        results[aid] = CheckInResult(
                            email=skip_email or f"account:{aid}",
                            success=False,
                            message=f"已跳过（{first_result.message}）",
                        )
                        self._record(
                            db,
                            r,
                            user_id,
                            aid,
                            data.courseid,
                            skip_email,
                            results[aid],
                        )

                    try:
                        db.commit()
                    except Exception as e:
                        logger.error("Failed to commit: %s", e)
                        db.rollback()

                    logger.info(
                        "Check-in aborted after canary failure — "
                        "%s succeeded, %s skipped",
                        0,
                        len(account_ids) - 1,
                    )
                    return results

                # ----- 签发前过滤已在 Redis 中标记已签到的账号 -----
                if len(account_ids) > 1:
                    dedup_filtered = []
                    already_done = []
                    for aid in account_ids:
                        if aid == first_aid:
                            continue
                        if r and r.get(f"checkin_done:{data.ticketid}:{aid}"):
                            already_done.append(aid)
                            email = self._resolve_client_email(snapshot, aid)
                            cr = CheckInResult(
                                email=email or f"account:{aid}",
                                success=True,
                                message="已签到（跳过重复调用）",
                            )
                            results[aid] = cr
                            self._record(
                                db,
                                r,
                                user_id,
                                aid,
                                data.courseid,
                                email,
                                cr,
                            )
                        else:
                            dedup_filtered.append(aid)
                    if already_done:
                        logger.info(
                            "Skipped %s accounts already checked in (ticket %s)",
                            len(already_done),
                            data.ticketid,
                        )
                    tasks = [
                        asyncio.create_task(
                            self._checkin_one_ensure(
                                snapshot,
                                db,
                                r,
                                user_id,
                                aid,
                                data,
                                client_ip=client_ip,
                            )
                        )
                        for aid in dedup_filtered
                    ]
                    gathered = await asyncio.gather(*tasks)
                    for aid, cr in gathered:
                        results[aid] = cr
                        logger.debug(
                            "Account %s check-in result: success=%s %s",
                            aid,
                            cr.success if cr else "N/A",
                            cr.message if cr else "",
                        )

                try:
                    db.commit()
                except Exception as e:
                    logger.error("Failed to commit: %s", e)
                    db.rollback()

            succeeded = sum(1 for r in results.values() if r is not None and r.success)
            logger.info(
                "Check-in completed for user=%s: %s/%s succeeded",
                user_id,
                succeeded,
                len(account_ids),
            )
            return results

    # ------------------------------------------------------------------
    # execute_checkin 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_canary(
        snapshot: dict[int, tuple[KetangPaiAPI, float] | None],
        account_ids: list[int],
    ) -> tuple[int, KetangPaiAPI] | None:
        """从快照中找到第一个有可用客户端的账号。"""
        for aid in account_ids:
            entry = snapshot.get(aid)
            if entry is not None:
                return (aid, entry[0])
        return None

    async def _checkin_one_ensure(
        self,
        snapshot: dict[int, tuple[KetangPaiAPI, float] | None],
        db,
        r,
        user_id: int,
        account_id: int,
        data: QRCheckInRequest,
        client_ip: str = "",
    ) -> tuple[int, CheckInResult | None]:
        """二维码签到单个账号（受 semaphore 限流），无会话时按需创建。

        :param client_ip: 客户端真实 IP，透传至课堂派签到请求。
        """
        async with self.semaphore:
            entry = snapshot.get(account_id)
            if entry is not None:
                client = entry[0]
            else:
                # 快照中无会话，尝试按需创建（to_thread 避免阻塞事件循环）
                logger.info(
                    "Account %s not in session pool, creating on demand",
                    account_id,
                )
                client = await asyncio.to_thread(
                    self._ensure_client,
                    account_id,
                    db_session=db,
                )
                if client is None:
                    email = self._resolve_client_email(snapshot, account_id)
                    return (
                        account_id,
                        CheckInResult(
                            email=email or f"account:{account_id}",
                            success=False,
                            message="签到失败：无法创建会话（账号不存在或登录失败）",
                        ),
                    )

            # 检查该 ticket 下此账号是否已签过
            dedup_key = f"checkin_done:{data.ticketid}:{account_id}"
            try:
                if r and r.get(dedup_key):
                    logger.info(
                        "Account %s already checked in for ticket %s — skipping",
                        account_id,
                        data.ticketid,
                    )
                    return (
                        account_id,
                        CheckInResult(
                            email=client.email,
                            success=True,
                            message="已签到（跳过重复调用）",
                        ),
                    )
            except Exception:
                pass  # Redis 不可用时放行

            try:
                result = await asyncio.to_thread(client.qr_check_in, data, client_ip)
            except Exception as e:
                logger.error(
                    "Check-in failed for account %s (%s): %s",
                    account_id,
                    client.email[:5] + "..." + client.email[-5:],
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
            # 签到成功后写入 Redis 去重标记
            if result.success:
                try:
                    ttl = max(data.expire - int(time.time()), 300)
                    r.set(dedup_key, "1", ttl)
                except Exception:
                    pass
            with self.lock:
                self._touch(account_id)
            self._record(
                db,
                r,
                user_id,
                account_id,
                data.courseid,
                client,
                result,
            )
            return (account_id, result)

    async def _checkin_all_ensure(
        self,
        snapshot: dict[int, tuple[KetangPaiAPI, float] | None],
        db,
        r,
        user_id: int,
        account_ids: list[int],
        data: QRCheckInRequest,
        client_ip: str = "",
    ) -> dict[int, CheckInResult | None]:
        """所有账号无会话时的兜底（二维码签到）：逐个尝试按需创建。

        :param client_ip: 客户端真实 IP，透传至课堂派签到请求。
        """
        logger.info(
            "Falling back to per-account ensure for all %s accounts", len(account_ids)
        )
        tasks = [
            asyncio.create_task(
                self._checkin_one_ensure(
                    snapshot,
                    db,
                    r,
                    user_id,
                    aid,
                    data,
                    client_ip=client_ip,
                )
            )
            for aid in account_ids
        ]
        gathered = await asyncio.gather(*tasks)
        results = {aid: cr for aid, cr in gathered}
        try:
            db.commit()
        except Exception as e:
            logger.error("Failed to commit: %s", e)
            db.rollback()
        return results

    # ------------------------------------------------------------------
    # GPS / 数字码签到
    # ------------------------------------------------------------------

    async def execute_gps_checkin(
        self,
        user_id: int,
        account_ids: list[int],
        data: CheckInRequest,
        client_ip: str = "",
    ) -> dict[int, CheckInResult | None]:
        """批量 GPS / 数字码签到入口。

        每个账号独立签到（无 canary 机制），通过 semaphore 限制并发。
        使用 data.id（考勤记录ID）做 Redis 去重。
        当经纬度为空时，自动用第一个可用会话预取建筑 GPS 坐标。

        :param client_ip: 客户端真实 IP，透传至课堂派签到请求。
        """
        logger.info(
            "Starting GPS check-in for user=%s accounts=%s attendance_id=%s",
            user_id,
            account_ids,
            data.id,
        )

        with self.lock:
            self._cleanup_expired()
            snapshot = {aid: self.clients.get(aid) for aid in account_ids}

        # ── 预取建筑 GPS 中心点 + 围栏半径（仅用快照中已有 client）──
        center_lat = data.latitude
        center_lng = data.longitude
        fence_radius = 0

        if not center_lat or not center_lng:
            # 先查 Redis 缓存
            cached = None
            coords_key = f"gps_coords:{data.id}"
            try:
                r_early = get_redis_client()
                if r_early:
                    cached = r_early.get(coords_key)
            except Exception:
                pass
            if cached:
                import json
                try:
                    parsed = json.loads(cached if isinstance(cached, str) else cached.decode())
                    if isinstance(parsed, dict):
                        center_lat = parsed.get("lat", "") or parsed.get("latitude", "")
                        center_lng = parsed.get("lng", "") or parsed.get("longitude", "")
                        logger.info(
                            "Using cached GPS coords for attendance %s: %s, %s",
                            data.id, center_lat, center_lng,
                        )
                except Exception:
                    pass

        # 取第一个可用 client（后续坐标和半径复用同一个）
        first_client = next(
            (entry[0] for entry in snapshot.values() if entry is not None),
            None,
        )

        if not center_lat or not center_lng:
            logger.info(
                "Coordinates empty, pre-fetching building GPS for attendance %s",
                data.id,
            )
            if first_client is not None:
                try:
                    gps_resp = first_client.get_attence_building_gps(data.id)
                    lat, lng = _extract_gps(gps_resp)
                    if lat is not None:
                        center_lat = lat
                    if lng is not None:
                        center_lng = lng
                    # 写入 Redis 缓存
                    if center_lat and center_lng:
                        try:
                            import json
                            get_redis_client().set(
                                f"gps_coords:{data.id}",
                                json.dumps({"lat": center_lat, "lng": center_lng}),
                                GPS_COORDS_CACHE_TTL,
                            )
                            logger.info(
                                "Cached GPS coords for attendance %s (TTL=%ss)",
                                data.id, GPS_COORDS_CACHE_TTL,
                            )
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning("Failed to pre-fetch building GPS: %s", e)

        # 尝试获取围栏半径（不论经纬度是否已提供）
        if first_client is not None:
            try:
                loc_resp = first_client.get_attence_location(data.id)
                fence_radius = _extract_radius(loc_resp)
            except Exception as e:
                logger.warning("Failed to pre-fetch fence radius: %s", e)

        async with self.exec_lock:
            results: dict[int, CheckInResult | None] = {}
            with get_session() as db:
                r = get_redis_client()

                # ── 检查该考勤是否已标记结束（在锁内，防竞态）──
                ended_key = f"gps_ended:{data.id}"
                try:
                    if r and r.get(ended_key):
                        logger.info("Attendance %s is cached as ended — skipping all accounts", data.id)
                        return {
                            aid: CheckInResult(
                                email=f"account:{aid}",
                                success=False,
                                message="已跳过（该GPS考勤已结束）",
                            )
                            for aid in account_ids
                        }
                except Exception:
                    pass

                dedup_filtered = []
                already_done = []
                for aid in account_ids:
                    dedup_key = f"checkin_done:gps:{data.id}:{aid}"
                    try:
                        if r and r.get(dedup_key):
                            already_done.append(aid)
                            email = self._resolve_client_email(snapshot, aid)
                            cr = CheckInResult(
                                email=email or f"account:{aid}",
                                success=True,
                                message="已签到（跳过重复调用）",
                            )
                            results[aid] = cr
                            self._record(db, r, user_id, aid, data.courseid, email, cr)
                        else:
                            dedup_filtered.append(aid)
                    except Exception:
                        dedup_filtered.append(aid)

                if already_done:
                    logger.info(
                        "Skipped %s accounts already checked in (attendance %s)",
                        len(already_done),
                        data.id,
                    )

                tasks = [
                    asyncio.create_task(
                        self._gps_checkin_one(
                            snapshot, db, r, user_id, aid, data, client_ip=client_ip,
                            center_lat=center_lat, center_lng=center_lng,
                            fence_radius=fence_radius,
                        )
                    )
                    for aid in dedup_filtered
                ]
                gathered = await asyncio.gather(*tasks)
                for aid, cr in gathered:
                    results[aid] = cr

                try:
                    db.commit()
                except Exception as e:
                    logger.error("Failed to commit: %s", e)
                    db.rollback()

            succeeded = sum(1 for r in results.values() if r is not None and r.success)
            logger.info(
                "GPS check-in completed for user=%s: %s/%s succeeded",
                user_id,
                succeeded,
                len(account_ids),
            )
            return results

    async def _gps_checkin_one(
        self,
        snapshot: dict[int, tuple[KetangPaiAPI, float] | None],
        db,
        r,
        user_id: int,
        account_id: int,
        data: CheckInRequest,
        client_ip: str = "",
        *,
        center_lat: str = "",
        center_lng: str = "",
        fence_radius: int = 0,
    ) -> tuple[int, CheckInResult | None]:
        """GPS 签到单个账号（受 semaphore 限流），无会话时按需创建。

        每个账号使用独立抖动的经纬度，使定位分布更自然。
        """
        async with self.semaphore:
            entry = snapshot.get(account_id)
            if entry is not None:
                client = entry[0]
            else:
                logger.info(
                    "Account %s not in session pool, creating on demand",
                    account_id,
                )
                client = await asyncio.to_thread(
                    self._ensure_client,
                    account_id,
                    db_session=db,
                )
                if client is None:
                    email = self._resolve_client_email(snapshot, account_id)
                    return (
                        account_id,
                        CheckInResult(
                            email=email or f"account:{account_id}",
                            success=False,
                            message="签到失败：无法创建会话（账号不存在或登录失败）",
                        ),
                    )

            # ── 经纬度随机抖动 ──
            if center_lat and center_lng and fence_radius > 0:
                jlat, jlng = _jitter_coordinates(
                    float(center_lat), float(center_lng), fence_radius
                )
                lat_str = f"{jlat:.8f}"
                lng_str = f"{jlng:.8f}"
            else:
                lat_str = data.latitude
                lng_str = data.longitude

            # 构造带抖动坐标的副本
            checkin_data = CheckInRequest(
                id=data.id,
                code=data.code,
                latitude=lat_str,
                longitude=lng_str,
            )

            dedup_key = f"checkin_done:gps:{data.id}:{account_id}"
            try:
                if r and r.get(dedup_key):
                    logger.info(
                        "Account %s already checked in for attendance %s — skipping",
                        account_id,
                        data.id,
                    )
                    return (
                        account_id,
                        CheckInResult(
                            email=client.email,
                            success=True,
                            message="已签到（跳过重复调用）",
                        ),
                    )
            except Exception:
                pass

            try:
                result = await asyncio.to_thread(
                    client.gps_check_in, checkin_data, client_ip
                )
            except Exception as e:
                logger.error(
                    "GPS check-in failed for account %s (%s): %s",
                    account_id,
                    client.email[:5] + "..." + client.email[-5:],
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

            if result.success:
                try:
                    r.set(dedup_key, "1", 86400)  # 默认 24h TTL
                except Exception:
                    pass

            # ── 位置错误 → 清除缓存的坐标，下次重新获取 ──
            if not result.success and is_position_error(result):
                try:
                    r.delete(f"gps_coords:{data.id}")
                    logger.info(
                        "Cleared cached GPS coords for attendance %s due to position error",
                        data.id,
                    )
                except Exception:
                    pass

            # ── 考勤已结束 → 缓存结束标记，整批次跳过 ──
            if not result.success and result.code == 30322:
                try:
                    r.set(f"gps_ended:{data.id}", "1", 3600)
                    logger.info("Cached ended state for attendance %s (TTL=3600s)", data.id)
                except Exception:
                    pass

            with self.lock:
                self._touch(account_id)
            self._record(
                db,
                r,
                user_id,
                account_id,
                data.courseid,
                client,
                result,
            )
            return (account_id, result)

    @staticmethod
    def _resolve_client_email(
        snapshot: dict[int, tuple[KetangPaiAPI, float] | None],
        account_id: int,
    ) -> str | None:
        """从快照中提取账号邮箱，无客户端时返回 None。"""
        entry = snapshot.get(account_id)
        if entry is not None:
            return entry[0].email
        return None

    @staticmethod
    def _build_skip_results(
        account_ids: list[int],
        message: str,
    ) -> dict[int, CheckInResult]:
        """为所有账号构建统一的"跳过"结果。"""
        return {
            aid: CheckInResult(
                email=f"account:{aid}",
                success=False,
                message=message,
            )
            for aid in account_ids
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _record(
        self,
        db,
        r,
        user_id,
        account_id,
        course_id,
        client_or_email,
        result,
    ):
        """记录签到日志并刷新 token 缓存（失败不抛异常）。

        *client_or_email* 可以是 KetangPaiAPI 实例（有 token）或纯邮箱字符串。
        """
        try:
            db.add(
                CheckInLog(
                    user_id=user_id,
                    account_id=account_id,
                    course_id=course_id,
                    status=1 if result.success else 0,
                    message=result.message,
                )
            )
        except Exception as e:
            logger.error("Failed to write CheckInLog: %s", e)
        if hasattr(client_or_email, "token") and client_or_email.token:
            try:
                r.set(
                    f"account:{account_id}:token",
                    client_or_email.token,
                    TOKEN_EXPIRE_TIME,
                )
            except Exception:
                pass


# 创建会话池（模块级单例）
session_pool = SessionPool()


# ── GPS 辅助函数 ──

import math
import random


def _extract_radius(resp: dict, default: int = 100) -> int:
    """从考勤位置 API 响应（已解包的 data 层）中提取围栏半径（米）。"""
    for key in ("radius", "range", "scope", "distance"):
        if key in resp:
            try:
                return int(resp[key])
            except (ValueError, TypeError):
                pass
    return default


def _jitter_coordinates(lat: float, lng: float, radius_meters: int) -> tuple[float, float]:
    """在围栏范围内随机抖动，最大偏移为中心到围栏边界的 60%。

    多个账号使用相同中心点时，每个账号得到独立抖动的坐标，
    使定位分布更自然，避免全部重合在同一坐标。
    """
    if radius_meters <= 0:
        return lat, lng

    max_offset = radius_meters * 0.6
    angle = random.random() * 2 * math.pi
    distance = random.random() * max_offset

    # 1 度纬度 ≈ 111320 m；1 度经度 ≈ 111320 * cos(lat) m
    dlat = distance * math.cos(angle) / 111320.0
    dlng = distance * math.sin(angle) / (111320.0 * math.cos(math.radians(lat)))

    return lat + dlat, lng + dlng
