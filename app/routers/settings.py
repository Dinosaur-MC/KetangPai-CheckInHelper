from redis import Redis
from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Body

from app.deps import get_current_user
from app.core.db import Session, get_redis, get_session_with
from app.models import BaseResponse, SystemSetting, User, Role

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["Settings"])


@router.get("/invite-required")
async def get_invite_required(
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    # Redis 缓存优先
    if redis:
        try:
            cached = redis.get("setting:invite_required")
            if cached is not None:
                return BaseResponse(
                    message="success", data={"invite_required": cached == b"true"}
                )
        except Exception as e:
            logger.warning("Redis 读取 invite_required 缓存失败: %s", e)
    setting = session.get(SystemSetting, "invite_required")
    val = setting and setting.value == "true"
    if redis:
        try:
            redis.set("setting:invite_required", "true" if val else "false", 604800)
        except Exception as e:
            logger.warning("Redis 写入 invite_required 缓存失败: %s", e)
    return BaseResponse(message="success", data={"invite_required": val})


@router.put("/invite-required")
async def set_invite_required(
    invite_required: bool = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")
    setting = session.get(SystemSetting, "invite_required")
    if setting is None:
        setting = SystemSetting(
            key="invite_required", value="true" if invite_required else "false"
        )
    else:
        setting.value = "true" if invite_required else "false"
    session.add(setting)
    # 更新 Redis 缓存
    if redis:
        try:
            redis.set(
                "setting:invite_required",
                "true" if invite_required else "false",
                604800,
            )
        except Exception as e:
            logger.warning("Redis 写入 invite_required 缓存失败: %s", e)
    return BaseResponse(message="设置已更新", data={"invite_required": invite_required})
