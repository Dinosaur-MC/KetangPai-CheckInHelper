from typing import Optional
from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Query
from sqlmodel import select

from app.deps import get_current_user
from app.core.db import Session, get_session_with
from app.utils import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate
from app.models import (
    BaseResponse,
    PaginatedResponse,
    User,
    Role,
    CheckInLog,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["Log"])


# ================================
#        CheckInLog CRUD
# ================================


@router.get("/checkin")
async def list_checkin_logs(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    account_id: Optional[int] = None,
    course_id: Optional[str] = None,
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """获取签到日志列表"""
    query = select(CheckInLog).where(CheckInLog.user_id == current_user.id)

    if account_id is not None:
        query = query.where(CheckInLog.account_id == account_id)

    if course_id is not None:
        query = query.where(CheckInLog.course_id == course_id)

    query = query.order_by(CheckInLog.created_at.desc())
    logs, total = paginate(session, query, page, page_size)
    return PaginatedResponse(
        message="success",
        data=[x.model_dump() for x in logs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/checkin/{log_id}")
async def get_checkin_log(
    log_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取指定签到日志"""
    log = session.get(CheckInLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="签到日志不存在")

    # 验证权限：日志的 user_id 必须等于当前用户
    if log.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权限访问此日志")

    return BaseResponse(message="success", data=log.model_dump())


@router.delete("/checkin/{log_id}")
async def delete_checkin_log(
    log_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """删除签到日志"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="无删除权限")

    log = session.get(CheckInLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="签到日志不存在")

    session.delete(log)
    session.flush()

    return BaseResponse(message="删除成功")
