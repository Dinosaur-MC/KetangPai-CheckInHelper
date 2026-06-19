import json
from starlette.exceptions import HTTPException
from fastapi import Request, Depends

from app.core.security import (
    decode_access_token,
    is_token_blacklisted,
)
from app.core.db import Session, Redis, get_session_with, get_redis
from app.models import User

import logging

logger = logging.getLogger(__name__)


# ================================
#               依赖
# ================================


def get_user_cache(redis: Redis, user_id: int) -> User | None:
    try:
        user_cache = redis.get(f"user:{user_id}")
        if user_cache is None:
            return None
        data = json.loads(user_cache)
        # 安全：仅校验必要字段，忽略 injected 字段
        return User.model_validate(data)
    except Exception:
        logger.warning("Redis unavailable — user cache miss for %s", user_id)
        return None


def get_current_user(
    request: Request,
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
) -> User | None:
    authorization = request.headers.get("Authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺失令牌")

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="认证令牌格式错误")

    # 检查 token 是否已被吊销
    token_jti = payload.get("jti")
    if token_jti and is_token_blacklisted(token_jti, redis):
        raise HTTPException(status_code=401, detail="令牌已被吊销")

    user_cache = get_user_cache(redis, user_id)
    if user_cache is not None:
        # 缓存命中时也检查 is_active
        if not user_cache.is_active:
            raise HTTPException(status_code=403, detail="账号已被禁用")
        return user_cache

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    if redis:
        try:
            redis.set(
                f"user:{user_id}",
                json.dumps(user.model_dump(exclude_none=True, mode="json")),
                86400,
            )
        except Exception:
            logger.warning("Failed to cache user %s in Redis", user_id)
    return user
