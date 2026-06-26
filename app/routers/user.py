import re
from typing import Optional
from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Body, Query
from sqlmodel import select, func

from app.deps import get_current_user
from app.core.security import (
    hash_password,
    verify_password,
    validate_password_strength,
)
from app.core.db import Session, Redis, get_session_with, get_redis
from app.utils import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate
from app.models import BaseResponse, User, Role, PaginatedResponse

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_last_admin(session: Session, user: User) -> bool:
    """检查 *user* 是否为系统中最后一个活跃管理员。"""
    if user.role != Role.admin:
        return False
    admin_count = session.exec(
        select(func.count(User.id)).where(
            User.role == Role.admin,
            User.is_active == True,
        )
    ).one()
    return admin_count <= 1


# ================================
#          User CRUD
# ================================


@router.get("/api/users/me")
async def get_my_info(
    current_user: User = Depends(get_current_user),
):
    """获取当前登录用户信息。"""
    return BaseResponse(data=current_user.model_dump(exclude=["password"]))


@router.get("/api/users")
async def list_users(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """获取所有用户列表（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")

    query = select(User).order_by(User.created_at.desc())
    users, total = paginate(session, query, page, page_size)
    return PaginatedResponse(
        message="success",
        data=[u.model_dump(exclude=["password"]) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/api/users/{user_id}")
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取指定用户信息"""
    if current_user.role != Role.admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="权限不足")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    return BaseResponse(
        message="success",
        data=user.model_dump(exclude=["password"]),
    )


@router.post("/api/users")
async def create_user(
    email: str = Body(...),
    password: str = Body(...),
    role: Role = Body(default=Role.user),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """创建新用户（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")

    # 验证邮箱格式
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")

    # 验证密码强度
    valid, msg = validate_password_strength(password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 检查邮箱是否已存在
    existing_user = session.exec(select(User).where(User.email == email)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    user = User(
        email=email,
        password=hash_password(password),
        role=role,
    )
    session.add(user)
    session.flush()

    return BaseResponse(
        message="success",
        data=user.model_dump(exclude=["password"]),
    )


@router.put("/api/users/{user_id}")
async def update_user(
    user_id: int,
    email: Optional[str] = Body(None),
    password: Optional[str] = Body(None),
    role: Optional[Role] = Body(None),
    is_active: Optional[bool] = Body(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    """更新用户信息"""
    if current_user.role != Role.admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="权限不足")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if email is not None:
        # 检查新邮箱是否已被其他用户使用
        existing_user = session.exec(
            select(User).where(User.email == email, User.id != user_id)
        ).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="邮箱已被使用")
        user.email = email

    if password is not None:
        valid, msg = validate_password_strength(password)
        if not valid:
            raise HTTPException(status_code=400, detail=msg)
        user.password = hash_password(password)

    if role is not None and current_user.role == Role.admin:
        if role != Role.admin and _is_last_admin(session, user):
            raise HTTPException(status_code=400, detail="不能降级最后一位管理员")
        user.role = role

    if is_active is not None and current_user.role == Role.admin:
        if not is_active and _is_last_admin(session, user):
            raise HTTPException(status_code=400, detail="不能停用最后一位管理员")
        user.is_active = is_active

    session.add(user)
    session.flush()

    # 清除缓存
    if redis:
        redis.delete(f"user:{user_id}")

    return BaseResponse(
        message="success",
        data=user.model_dump(exclude=["password"]),
    )


@router.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    """删除用户（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")

    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if _is_last_admin(session, user):
        raise HTTPException(status_code=400, detail="不能删除最后一位管理员")

    session.delete(user)
    session.flush()

    # 清除缓存
    if redis:
        redis.delete(f"user:{user_id}")

    return BaseResponse(message="删除成功")


# ================================
#       当前用户 — 修改密码
# ================================


@router.put("/api/user/password")
async def change_password(
    old_password: str = Body(...),
    new_password: str = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    """修改当前登录用户的密码"""
    # 1. 验证旧密码
    if not verify_password(old_password, current_user.password):
        raise HTTPException(status_code=400, detail="旧密码不正确")

    # 2. 验证新密码强度
    valid, msg = validate_password_strength(new_password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 3. 新旧密码不能相同
    if old_password == new_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")

    # 4. 更新密码
    current_user.password = hash_password(new_password)
    session.add(current_user)
    session.flush()

    # 5. 清除 Redis 用户缓存
    if redis:
        redis.delete(f"user:{current_user.id}")

    return BaseResponse(message="密码修改成功")
