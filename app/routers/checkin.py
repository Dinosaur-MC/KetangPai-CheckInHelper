from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Request
from sqlmodel import select

from app.core.api import QRCheckInRequest, CheckInRequest, CheckInResult
from app.deps import get_current_user
from app.core.db import Session, get_session_with
from app.models import BaseResponse, User, Account, UserAccount, CourseBinding, AutoCheckinConfig

from datetime import datetime, timezone
from pydantic import BaseModel

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


class AutoCheckinConfigBody(BaseModel):
    enabled: bool = False
    checkin_types: str = "1,2"
    time_windows: str = '[{"start":7,"end":22}]'


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
                  "time_windows": [{"start": 7, "end": 22}]},
        )
    try:
        windows = json.loads(config.time_windows)
    except Exception as e:
        logger.warning("解析 time_windows JSON 失败: %s, raw=%r", e, config.time_windows)
        windows = [{"start": 7, "end": 22}]
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
    try:
        windows = json.loads(body.time_windows) if isinstance(body.time_windows, str) else body.time_windows
        if not isinstance(windows, list) or not windows:
            windows = [{"start": 7, "end": 22}]
        for w in windows:
            w["start"] = max(0, min(23, int(w.get("start", 7))))
            w["end"] = max(0, min(23, int(w.get("end", 22))))
        # 去重：相同 start/end 的时段只保留一个
        seen = set()
        deduped = []
        for w in windows:
            key = f"{w['start']}-{w['end']}"
            if key not in seen:
                seen.add(key)
                deduped.append(w)
        windows = deduped
    except Exception as e:
        logger.warning("解析请求 time_windows 失败: %s, raw=%r", e, body.time_windows)
        windows = [{"start": 7, "end": 22}]
    time_windows_str = json.dumps(windows, ensure_ascii=False)

    config = session.exec(
        select(AutoCheckinConfig).where(AutoCheckinConfig.user_id == current_user.id)
    ).first()
    if config is None:
        config = AutoCheckinConfig(
            user_id=current_user.id,
            enabled=body.enabled,
            checkin_types=body.checkin_types,
            time_windows=time_windows_str,
        )
        session.add(config)
    else:
        config.enabled = body.enabled
        config.checkin_types = body.checkin_types
        config.time_windows = time_windows_str
        config.updated_at = datetime.now(timezone.utc)
        session.add(config)
    session.commit()
    logger.info("Auto-checkin config saved user=%s enabled=%s types=%s windows=%s",
                current_user.id, body.enabled, body.checkin_types, time_windows_str)
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
):
    from app.core.watcher import auto_checkin_watcher

    return BaseResponse(data=auto_checkin_watcher.get_status(), message="success")


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
