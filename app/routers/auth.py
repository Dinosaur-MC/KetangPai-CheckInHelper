import re
import time
from datetime import datetime, timezone
from starlette.exceptions import HTTPException
from fastapi import APIRouter, Request, Depends, Body
from sqlmodel import select

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    hash_password,
    verify_password,
    validate_password_strength,
    blacklist_token,
)
from app.core.db import Session, Redis, get_session_with, get_redis
from app.utils import RateLimiter

from app.models import BaseResponse, SystemSetting, User, InviteCode

import logging

logger = logging.getLogger(__name__)

# 登录/注册接口每分钟最多 5 次请求
auth_rate_limiter = RateLimiter(times=5, seconds=60)

router = APIRouter()


@router.post("/api/register")
async def register(
    email: str = Body(...),
    password: str = Body(...),
    invite_code: str = Body(default=""),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
    _rate_limit: None = Depends(auth_rate_limiter),
):
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")
    valid, msg = validate_password_strength(password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 先验证邀请码（Redis 缓存优先），再查用户
    invite_required = False
    try:
        if redis:
            cached = redis.get("setting:invite_required")
            if cached is not None:
                invite_required = cached == b"true"
    except Exception:
        pass
    if not invite_required:
        setting = session.get(SystemSetting, "invite_required")
        invite_required = setting and setting.value == "true"

    matched_code = None
    if invite_required:
        if not invite_code:
            raise HTTPException(status_code=400, detail="注册需要邀请码")
        matched_code = session.exec(
            select(InviteCode).where(
                InviteCode.code == invite_code.strip().upper(),
                InviteCode.is_active == True,
            )
        ).first()
        if matched_code is None or len(matched_code.code) != 16:
            raise HTTPException(status_code=400, detail="邀请码无效")
        if (
            matched_code.max_uses is not None
            and matched_code.used_count >= matched_code.max_uses
        ):
            raise HTTPException(status_code=400, detail="邀请码已用完")
        if matched_code.expires_at and matched_code.expires_at.replace(
            tzinfo=None
        ) < datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(status_code=400, detail="邀请码已过期")
    elif invite_code:
        # 选填模式，仅做记录
        matched_code = session.exec(
            select(InviteCode).where(
                InviteCode.code == invite_code.strip().upper(),
                InviteCode.is_active == True,
            )
        ).first()

    # 再查用户（避免暴露已注册邮箱给未验证的邀请码请求）
    user = session.exec(select(User).where(User.email == email)).first()
    if user is not None:
        raise HTTPException(status_code=400, detail="用户已存在")

    # 创建用户
    user = User(email=email, password=hash_password(password))
    session.add(user)
    session.flush()
    session.refresh(user)

    # 注册成功后才记录邀请码使用
    if matched_code:
        if (
            matched_code.max_uses is None
            or matched_code.used_count < matched_code.max_uses
        ):
            if not matched_code.expires_at or matched_code.expires_at.replace(
                tzinfo=None
            ) >= datetime.now(timezone.utc).replace(tzinfo=None):
                matched_code.used_count += 1
                session.add(matched_code)

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    return BaseResponse(
        message="注册成功",
        data={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
            },
        },
    )


@router.post("/api/login")
async def login(
    email: str = Body(...),
    password: str = Body(...),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(auth_rate_limiter),
):
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少为 8 个字符")

    user = session.exec(select(User).where(User.email == email)).first()
    if user is None or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.flush()

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    return BaseResponse(
        message="登录成功",
        data={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
            },
        },
    )


@router.post("/api/logout")
async def logout(
    request: Request,
    redis: Redis = Depends(get_redis),
) -> BaseResponse:
    """登出 — 将当前 token 加入黑名单"""
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = decode_access_token(token)
        if payload and payload.get("jti"):
            jti = payload["jti"]
            exp = payload.get("exp", 0)
            ttl = max(int(exp - time.time()), 60)
            blacklist_token(jti, redis, ttl=ttl)
    return BaseResponse(message="已登出")


@router.post("/api/refresh")
async def refresh_token(
    request: Request,
    redis: Redis = Depends(get_redis),
    session: Session = Depends(get_session_with),
) -> BaseResponse:
    """刷新令牌 — 使用 refresh_token 换取新的 access_token + refresh_token。

    采用 rotation 策略：旧的 refresh_token 被标记为已使用，无法再次刷新。
    """
    authorization = request.headers.get("Authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺失令牌")

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_refresh_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")

    jti = payload.get("jti")
    user_id = payload.get("sub")
    if not jti or not user_id:
        raise HTTPException(status_code=401, detail="刷新令牌格式错误")

    # 检查 refresh token 是否已被使用（rotation 防重用）
    if redis and redis.exists(f"refresh_used:{jti}"):
        raise HTTPException(status_code=401, detail="刷新令牌已被使用")

    # 标记当前 refresh token 为已使用
    exp = payload.get("exp", 0)
    ttl = max(int(exp - time.time()), 86400)
    if redis:
        redis.setex(f"refresh_used:{jti}", ttl, "1")

    # 验证用户仍然存在且活跃
    user = session.get(User, int(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    # 签发新令牌对
    new_access = create_access_token(user_id)
    new_refresh = create_refresh_token(user_id)
    return BaseResponse(
        data={
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
            },
        },
    )
