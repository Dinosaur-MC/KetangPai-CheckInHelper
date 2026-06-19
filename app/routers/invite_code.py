from datetime import datetime, timedelta, timezone
from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Body, Query
from sqlmodel import select

from app.deps import get_current_user
from app.core.db import Session, get_session_with
from app.utils import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate
from app.models import (
    BaseResponse,
    PaginatedResponse,
    User,
    Role,
    InviteCode,
    generate_invite_code,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invite-codes", tags=["InviteCode"])

# ================================
#          邀请码管理
# ================================


@router.get("")
async def list_invite_codes(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")
    query = select(InviteCode).order_by(InviteCode.created_at.desc())
    codes, total = paginate(session, query, page, page_size)
    return PaginatedResponse(
        message="success",
        data=[c.model_dump() for c in codes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("")
async def create_invite_code(
    code: str = Body(default=""),
    max_uses: int | None = Body(default=None),
    expires_in_hours: int | None = Body(default=None),
    note: str = Body(default=""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")
    ic = InviteCode(
        code=code.strip().upper() if code.strip() else generate_invite_code(),
        max_uses=max_uses,
        expires_at=(
            (
                datetime.now(timezone.utc).replace(second=0, microsecond=0)
                + timedelta(hours=expires_in_hours)
            )
            if expires_in_hours
            else None
        ),
        note=note,
        created_by=current_user.id,
    )
    session.add(ic)
    session.flush()
    session.refresh(ic)
    return BaseResponse(message="邀请码已创建", data=ic.model_dump())


@router.put("/{code_id}")
async def update_invite_code(
    code_id: int,
    is_active: bool = Body(default=True),
    max_uses: int | None = Body(default=None),
    note: str = Body(default=""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")
    ic = session.get(InviteCode, code_id)
    if ic is None:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    ic.is_active = is_active
    ic.max_uses = max_uses
    ic.note = note
    session.add(ic)
    return BaseResponse(message="邀请码已更新", data=ic.model_dump())


@router.delete("/{code_id}")
async def delete_invite_code(
    code_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")
    ic = session.get(InviteCode, code_id)
    if ic is None:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    session.delete(ic)
    return BaseResponse(message="邀请码已删除")
