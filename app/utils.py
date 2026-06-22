from starlette.exceptions import HTTPException
from fastapi import Request, Depends

from app.core.db import Session, Redis, get_redis
from sqlmodel import select, func
from sqlmodel.sql.expression import Select, SelectOfScalar
from sqlmodel import SQLModel

import logging

logger = logging.getLogger(__name__)

# ================================
#            速率限制
# ================================


def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP，优先信任反向代理的 X-Forwarded-For 头。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Redis-based rate limiter dependency."""

    def __init__(self, times: int, seconds: int):
        self.times = times
        self.seconds = seconds

    async def __call__(self, request: Request, redis: Redis = Depends(get_redis)):
        client_ip = get_client_ip(request)
        key = f"rate_limit:{request.url.path}:{client_ip}"
        try:
            current = redis.incr(key)
            if current == 1:
                redis.expire(key, self.seconds)
        except Exception:
            return  # Redis 不可用或 connection=None 时放行
        if current > self.times:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


# ================================
#         分页工具
# ================================

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200


def paginate[T: SQLModel](
    session: Session, query: Select[T] | SelectOfScalar[T], page: int, page_size: int
):
    """对 SQLModel select 查询应用分页，返回 (items, total_count)。"""
    # 防御性校验
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = DEFAULT_PAGE_SIZE
    # 先获取总数（移除 ORDER BY 提高性能）
    # 使用 session.execute()（Core 级）获取标量计数，避免 session.exec()
    # 不必要的 ORM 实体包装层。
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = session.execute(count_query).scalar_one()
    # 应用 OFFSET / LIMIT
    items = session.exec(query.offset((page - 1) * page_size).limit(page_size)).all()
    return items, total
