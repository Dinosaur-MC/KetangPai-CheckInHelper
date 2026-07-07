from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select
from pydantic import BaseModel

from app.deps import get_current_user
from app.core.db import Session, get_session_with
from app.models import BaseResponse, User, Account, Course, CourseLocation, UserAccount
from app.utils import RateLimiter

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/course-locations")
async def get_course_locations(
    course_id: str = Query(default=""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(RateLimiter(times=60, seconds=60)),
):
    """查询当前用户所有课程的位置记录，可选按课程筛选。"""
    query = (
        select(CourseLocation, Account.email, Course.course_name)
        .join(Account, CourseLocation.account_id == Account.id)
        .join(Course, CourseLocation.course_id == Course.id)
        .join(UserAccount, Account.id == UserAccount.account_id)
        .where(UserAccount.user_id == current_user.id)
    )
    if course_id:
        query = query.where(CourseLocation.course_id == course_id)

    rows = session.exec(query).all()
    data = [
        {
            "id": loc.id,
            "course_id": loc.course_id,
            "course_name": course_name,
            "account_id": loc.account_id,
            "account_email": email,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "address": loc.address,
        }
        for loc, email, course_name in rows
    ]
    return BaseResponse(data=data)


class CourseLocationBody(BaseModel):
    course_id: str
    account_id: int
    latitude: str
    longitude: str
    address: str = ""


@router.put("/api/course-locations")
async def upsert_course_location(
    body: CourseLocationBody,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(RateLimiter(times=60, seconds=60)),
):
    """创建或更新一条位置记录（UPSERT）。"""
    # 验证账号属于当前用户
    account = session.exec(
        select(Account)
        .join(UserAccount)
        .where(
            Account.id == body.account_id,
            UserAccount.user_id == current_user.id,
        )
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在或不属于当前用户")

    loc = session.exec(
        select(CourseLocation).where(
            CourseLocation.course_id == body.course_id,
            CourseLocation.account_id == body.account_id,
        )
    ).first()

    if loc:
        loc.latitude = body.latitude
        loc.longitude = body.longitude
        loc.address = body.address
        loc.updated_at = datetime.now(timezone.utc)
    else:
        loc = CourseLocation(
            course_id=body.course_id,
            account_id=body.account_id,
            latitude=body.latitude,
            longitude=body.longitude,
            address=body.address,
        )
        session.add(loc)

    session.commit()
    session.refresh(loc)
    logger.info("CourseLocation saved: course=%s account=%s", body.course_id, body.account_id)
    return BaseResponse(data={
        "id": loc.id,
        "course_id": loc.course_id,
        "account_id": loc.account_id,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "address": loc.address,
    }, message="位置已保存")


@router.delete("/api/course-locations")
async def delete_course_location(
    course_id: str = Query(...),
    account_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(RateLimiter(times=60, seconds=60)),
):
    """删除一条位置记录。"""
    loc = session.exec(
        select(CourseLocation).where(
            CourseLocation.course_id == course_id,
            CourseLocation.account_id == account_id,
        )
    ).first()
    if not loc:
        raise HTTPException(status_code=404, detail="位置记录不存在")

    session.delete(loc)
    session.commit()
    logger.info("CourseLocation deleted: course=%s account=%s", course_id, account_id)
    return BaseResponse(message="位置已删除")
