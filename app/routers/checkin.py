from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Request
from sqlmodel import select

from app.api import CheckInRequest, CheckInResult
from app.deps import get_current_user
from app.core.db import Session, get_session_with
from app.models import BaseResponse, User, Account, UserAccount, CourseBinding

import logging

from app.utils import RateLimiter, get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()


# ================================
#             CheckIn
# ================================


@router.post("/api/checkin")
async def check_in(
    data: CheckInRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(RateLimiter(times=10, seconds=60)),
):
    import asyncio
    from app.core.sessions import session_pool

    client_ip = get_client_ip(request)

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
    return BaseResponse(
        data={
            "success_count": success_count,
            "results": [r.model_dump() for r in result.values() if r is not None],
        },
        message=f"成功签到{success_count}个账号",
    )
