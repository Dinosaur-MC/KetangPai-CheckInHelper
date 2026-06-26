"""自动 GPS / 数字签到观察器。

后台轮询启用了自动签到的用户的所有绑定课程，
发现未完成的 GPS / 数字考勤后自动执行签到。
"""

import asyncio
import json
import random
import time
import logging
from datetime import datetime, timezone

from sqlmodel import select

from app.core.db import get_session, get_redis_client
from pydantic import BaseModel
from app.core.api import CheckInRequest
from app.models import AutoCheckinConfig, User, Account, UserAccount, CourseBinding
from app.core.sessions import session_pool

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60       # 轮询间隔（秒）
MIN_DELAY = 2            # 执行前最小随机延迟（秒）
MAX_DELAY = 8            # 执行前最大随机延迟（秒）
DEDUP_TTL = 86400        # 去重标记 TTL（24h）


def _in_time_windows(time_windows_str: str, now_hour: int) -> bool:
    """检查当前小时是否在 time_windows JSON 的某个时段内。"""
    try:
        windows = json.loads(time_windows_str)
        for w in windows:
            start = int(w.get("start", 7))
            end = int(w.get("end", 22))
            if start <= now_hour < end:
                return True
    except Exception as e:
        logger.warning("解析 time_windows JSON 失败: %s, raw=%r", e, time_windows_str)
    return False


class UserCheckinPlan(BaseModel):
    """单个用户的自动签到计划"""
    user_id: int
    allowed_types: set[str]
    courses: dict[str, list[int]]  # course_id -> [account_ids]


class AutoCheckinWatcher:
    """后台自动签到观察器。"""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

        # 公开状态（前端可查）
        self.is_running = False
        self.last_tick_time: float = 0.0
        self.last_result: dict | None = None  # {"checked": N, "succeeded": N}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self):
        if self._running:
            return
        self._running = True
        self.is_running = True
        self._task = asyncio.create_task(self._loop(), name="auto-checkin-watcher")
        logger.info("Auto-checkin watcher started")

    async def stop(self):
        self._running = False
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Auto-checkin watcher stopped")

    async def trigger(self):
        """手动触发一次扫描，忽略轮询间隔。"""
        if not self._running:
            logger.warning("Watcher not running, cannot trigger")
            return
        await self._tick()

    def get_status(self) -> dict:
        now = datetime.now()
        return {
            "last_tick_time": datetime.fromtimestamp(self.last_tick_time, tz=timezone.utc).isoformat()
                if self.last_tick_time else None,
            "last_result": self.last_result or {},
            "current_hour": now.hour,
        }

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _loop(self):
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Auto-checkin tick failed: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self):
        """单次扫描：查所有启用自动签到的用户，处理其绑定课程的未完成签到。"""
        logger.debug("Auto-checkin tick start")

        checked = 0
        succeeded = 0

        with get_session() as db:
            now_hour = datetime.now().hour

            # 1. 查询配置 + 用户，过滤时间范围后再查绑定和创建会话
            all_rows = db.exec(
                select(AutoCheckinConfig, User)
                .join(User, AutoCheckinConfig.user_id == User.id)
                .where(
                    AutoCheckinConfig.enabled == True,
                    User.is_active == True,
                )
            ).all()

            plans: list[UserCheckinPlan] = []
            for config, user in all_rows:
                if _in_time_windows(config.time_windows, now_hour):
                    plans.append(UserCheckinPlan(
                        user_id=user.id,
                        allowed_types=set(config.checkin_types.split(",")),
                        courses={},
                    ))

            if not plans:
                logger.debug("No active auto-checkin plans in current time window")
                self.last_tick_time = time.time()
                self.last_result = {"checked": 0, "succeeded": 0}
                return

            # 2. 只查询活跃用户的绑定 + 账号
            active_ids = [p.user_id for p in plans]
            bindings = db.exec(
                select(CourseBinding, Account, UserAccount)
                .join(Account, CourseBinding.account_id == Account.id)
                .join(UserAccount, Account.id == UserAccount.account_id)
                .where(
                    CourseBinding.is_active == True,
                    UserAccount.user_id.in_(active_ids),
                )
            ).all()

            all_accounts: list[Account] = []
            seen_ids = set()
            for binding, account, ua in bindings:
                for plan in plans:
                    if plan.user_id == ua.user_id:
                        plan.courses.setdefault(binding.course_id, []).append(account.id)
                        break
                if account.id not in seen_ids:
                    seen_ids.add(account.id)
                    all_accounts.append(account)

            if all_accounts:
                await session_pool.create(all_accounts)

            # 3. 执行签到
            for plan in plans:
                if not plan.courses:
                    continue

                for course_id, account_ids in plan.courses.items():
                    if not account_ids:
                        continue

                    # 尝试多个账号查询考勤列表，任一可用即可
                    attence_list = []
                    first_client = None
                    for aid in account_ids:
                        try:
                            client = await session_pool.ensure_client(aid)
                            if client is None:
                                continue
                            attence_list = await client.get_not_finish_attence_student(course_id)
                            first_client = client
                            if attence_list:
                                break
                        except Exception as e:
                            logger.warning(
                                "Failed to query attence for course %s via account %s: %s",
                                course_id, aid, e,
                            )
                            continue
                    else:
                        # 所有账号均失败
                        continue

                    for att in attence_list:
                        att_type = att.get("type", "")
                        att_id = att.get("id", "")
                        if not att_id or att_type not in plan.allowed_types:
                            continue

                        checked += 1
                        delay = random.uniform(MIN_DELAY, MAX_DELAY)
                        logger.info(
                            "Auto check-in: type=%s attendance=%s course=%s user=%s delay=%.1fs accounts=%s",
                            att_type, att_id, course_id, plan.user_id, delay, account_ids,
                        )
                        await asyncio.sleep(delay)

                        try:
                            checkin_data = CheckInRequest(id=att_id, courseid=course_id)
                            if att_type == "1" and first_client is not None:
                                code = await first_client.get_digit_attence(att_id)
                                if not code:
                                    logger.warning("Empty digit code for attendance %s, skipping", att_id)
                                    continue
                                checkin_data.code = code

                            result = await session_pool.execute_gps_checkin(
                                user_id=plan.user_id,
                                account_ids=account_ids,
                                data=checkin_data,
                                client_ip="",
                            )
                            success_count = sum(1 for r in result.values() if r is not None and r.success)
                            succeeded += success_count
                            logger.info(
                                "Auto check-in result: attendance=%s type=%s success=%s/%s",
                                att_id, att_type, success_count, len(account_ids),
                            )
                        except Exception as e:
                            logger.exception("Auto check-in failed for attendance %s: %s", att_id, e)

        self.last_tick_time = time.time()
        self.last_result = {"checked": checked, "succeeded": succeeded}
        logger.info("Auto-checkin tick done: checked=%s succeeded=%s", checked, succeeded)


# 模块级单例
auto_checkin_watcher = AutoCheckinWatcher()
