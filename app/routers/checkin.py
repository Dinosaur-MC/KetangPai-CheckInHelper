from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Request
from sqlmodel import select

from app.core.api import QRCheckInRequest, CheckInRequest, CheckInResult
from app.deps import get_current_user
from app.core.db import Session, get_session_with
from app.models import BaseResponse, User, Account, UserAccount, CourseBinding, AutoCheckinConfig

from datetime import datetime, timezone
from pydantic import BaseModel, field_validator, model_validator

import json
import logging

from app.utils import RateLimiter, get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()


# ================================
#             CheckIn
# ================================


@router.post("/api/checkin")
async def check_in(
    data: QRCheckInRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(RateLimiter(times=60, seconds=60)),
):
    import asyncio
    from app.core.sessions import session_pool

    client_ip = get_client_ip(request)
    logger.info("QR check-in request user=%s course=%s ticket=%s ip=%s",
                current_user.id, data.courseid, data.ticketid, client_ip)

    course_id = data.courseid
    accounts = session.exec(
        select(Account)
        .join(UserAccount)
        .join(CourseBinding)
        .where(
            UserAccount.user_id == current_user.id,
            CourseBinding.course_id == course_id,
            CourseBinding.is_active == True,
        )
    ).all()
    if not accounts:
        raise HTTPException(status_code=404, detail="无绑定此课程的账号")

    if not await asyncio.to_thread(session_pool.create, accounts):
        return BaseResponse(code=500, message="创建会话失败")

    account_ids = [a.id for a in accounts]
    result: dict[int, CheckInResult | None] = await session_pool.execute_checkin(
        current_user.id, account_ids, data, client_ip=client_ip
    )

    success_count = sum(1 for r in result.values() if r is not None and r.success)
    logger.info("QR check-in done user=%s success=%s/%s ip=%s",
                current_user.id, success_count, len(account_ids), client_ip)
    return BaseResponse(
        data={
            "success_count": success_count,
            "results": [r.model_dump() for r in result.values() if r is not None],
        },
        message=f"成功签到{success_count}个账号",
    )


@router.post("/api/checkin/gps")
async def gps_check_in(
    data: CheckInRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(RateLimiter(times=60, seconds=60)),
):
    import asyncio
    from app.core.sessions import session_pool

    client_ip = get_client_ip(request)
    logger.info("GPS check-in request user=%s course=%s attendance=%s ip=%s",
                current_user.id, data.courseid, data.id, client_ip)

    course_id = data.courseid
    if not course_id:
        raise HTTPException(status_code=422, detail="缺少 courseid 参数")

    accounts = session.exec(
        select(Account)
        .join(UserAccount)
        .join(CourseBinding)
        .where(
            UserAccount.user_id == current_user.id,
            CourseBinding.course_id == course_id,
            CourseBinding.is_active == True,
        )
    ).all()
    if not accounts:
        raise HTTPException(status_code=404, detail="无绑定此课程的账号")

    if not await asyncio.to_thread(session_pool.create, accounts):
        return BaseResponse(code=500, message="创建会话失败")

    account_ids = [a.id for a in accounts]
    result: dict[int, CheckInResult | None] = await session_pool.execute_gps_checkin(
        current_user.id, account_ids, data, client_ip=client_ip
    )

    success_count = sum(1 for r in result.values() if r is not None and r.success)
    logger.info("GPS check-in done user=%s success=%s/%s ip=%s",
                current_user.id, success_count, len(account_ids), client_ip)
    if success_count:
        msg = f"成功签到{success_count}个账号"
    else:
        msg = "签到失败，所有账号均未成功"
    return BaseResponse(
        data={
            "success_count": success_count,
            "results": [r.model_dump() for r in result.values() if r is not None],
        },
        message=msg,
    )


# ================================
#         Auto CheckIn
# ================================


class TimeWindow(BaseModel):
    """单个运行时段，严格校验。"""
    start: int
    end: int

    @field_validator("start", "end")
    @classmethod
    def hour_range(cls, v):
        if v < 0 or v > 23:
            raise ValueError("时段小时必须在 0-23 之间")
        return v

    @model_validator(mode="after")
    def start_before_end(self):
        if self.start >= self.end:
            raise ValueError("start 必须小于 end")
        return self


VALID_CHECKIN_TYPES = {"1", "2"}


class AutoCheckinConfigBody(BaseModel):
    enabled: bool = False
    checkin_types: str = "1,2"
    time_windows: str = '[]'

    @field_validator("checkin_types")
    @classmethod
    def validate_checkin_types(cls, v):
        parts = [s.strip() for s in v.split(",") if s.strip()]
        if not parts:
            raise ValueError("请至少选择一种签到类型")
        invalid = set(parts) - VALID_CHECKIN_TYPES
        if invalid:
            raise ValueError(f"无效的签到类型: {', '.join(sorted(invalid))}，可选 1(数字考勤) 2(GPS考勤)")
        # 去重并排序
        return ",".join(sorted(set(parts)))

    @field_validator("time_windows", mode="before")
    @classmethod
    def validate_time_windows(cls, v):
        # 解析 JSON 字符串或列表
        raw = json.loads(v) if isinstance(v, str) else v
        if not isinstance(raw, list):
            raise ValueError("time_windows 必须是数组")
        if len(raw) > 16:
            raise ValueError("时段数量不能超过 16 个")
        # 逐个校验并去重
        seen = set()
        validated = []
        for item in raw:
            tw = TimeWindow(**item)
            key = f"{tw.start}-{tw.end}"
            if key not in seen:
                seen.add(key)
                validated.append(tw.model_dump())
        return json.dumps(validated, ensure_ascii=False)


@router.get("/api/auto-checkin/config")
async def get_auto_checkin_config(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    config = session.exec(
        select(AutoCheckinConfig).where(AutoCheckinConfig.user_id == current_user.id)
    ).first()
    if config is None:
        return BaseResponse(
            message="success",
            data={"enabled": False, "checkin_types": "1,2",
                  "time_windows": []},
        )
    try:
        windows = json.loads(config.time_windows)
    except Exception as e:
        logger.warning("解析 time_windows JSON 失败: %s, raw=%r", e, config.time_windows)
        windows = []
    return BaseResponse(
        message="success",
        data={
            "enabled": config.enabled,
            "checkin_types": config.checkin_types,
            "time_windows": windows,
            "created_at": config.created_at.isoformat(),
            "updated_at": config.updated_at.isoformat(),
        },
    )


@router.put("/api/auto-checkin/config")
async def update_auto_checkin_config(
    body: AutoCheckinConfigBody,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    # body.time_windows 已在 Pydantic 中完成校验、清洗、去重
    config = session.exec(
        select(AutoCheckinConfig).where(AutoCheckinConfig.user_id == current_user.id)
    ).first()
    if config is None:
        config = AutoCheckinConfig(
            user_id=current_user.id,
            enabled=body.enabled,
            checkin_types=body.checkin_types,
            time_windows=body.time_windows,
        )
        session.add(config)
    else:
        config.enabled = body.enabled
        config.checkin_types = body.checkin_types
        config.time_windows = body.time_windows
        config.updated_at = datetime.now(timezone.utc)
        session.add(config)
    session.commit()
    logger.info("Auto-checkin config saved user=%s enabled=%s types=%s windows=%s",
                current_user.id, body.enabled, body.checkin_types, body.time_windows)
    return BaseResponse(
        data={
            "enabled": config.enabled,
            "checkin_types": config.checkin_types,
            "time_windows": json.loads(config.time_windows),
        },
        message="自动签到配置已更新",
    )


@router.get("/api/auto-checkin/status")
async def get_auto_checkin_status(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    from app.core.watcher import auto_checkin_watcher

    status = auto_checkin_watcher.get_status()
    # 查询当前用户的配置，判断自动签到是否实际生效
    config = session.exec(
        select(AutoCheckinConfig).where(AutoCheckinConfig.user_id == current_user.id)
    ).first()
    active = False
    if config and config.enabled:
        try:
            windows = json.loads(config.time_windows)
            active = bool(windows)
        except Exception:
            active = False
    status["user_active"] = active
    return BaseResponse(data=status, message="success")


@router.post("/api/auto-checkin/trigger")
async def trigger_auto_checkin(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    from app.core.watcher import auto_checkin_watcher

    config = session.exec(
        select(AutoCheckinConfig).where(AutoCheckinConfig.user_id == current_user.id)
    ).first()
    if not config or not config.enabled:
        return BaseResponse(code=400, message="请先开启自动签到")
    logger.info("Auto-checkin trigger user=%s", current_user.id)
    await auto_checkin_watcher.trigger()
    return BaseResponse(message="扫描已触发")
