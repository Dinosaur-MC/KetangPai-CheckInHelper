"""
SQLModel + MySQL + Redis
"""

import os
from sqlmodel import create_engine, SQLModel, Session
from redis import Redis, ConnectionPool, ConnectionError
from app.models import *

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://checkinhelper:checkinhelper@localhost:3306/checkinhelper?charset=utf8mb4",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

DB_ECHO = os.getenv("DB_ECHO", "false").lower() in ("1", "true", "yes")

engine = create_engine(
    DATABASE_URL,
    echo=DB_ECHO,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "3600")),
)

_redis_pool: "ConnectionPool | None" = None


def _get_redis_pool() -> "ConnectionPool | None":
    """懒初始化 Redis 连接池。连接失败返回 None。"""
    global _redis_pool
    if _redis_pool is None:
        try:
            _redis_pool = ConnectionPool.from_url(REDIS_URL, protocol=2)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Redis 连接失败，缓存与速率限制将不可用。请检查 REDIS_URL=%s",
                REDIS_URL,
            )
            return None
    return _redis_pool


def init_db():
    """在应用启动时调用，确保表已创建（幂等）。"""
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


def get_session_with():
    with Session(engine) as session:
        try:
            yield session
            session.commit()  # commit before __exit__ calls close()
        except Exception:
            session.rollback()
            raise


def get_redis():
    pool = _get_redis_pool()
    if pool is None:
        yield None
        return
    r = Redis(connection_pool=pool)
    try:
        yield r
    finally:
        r.close()


def get_redis_client() -> "Redis | None":
    """Return a Redis client directly (non-generator, for use outside FastAPI DI).

    如果 Redis 不可用，返回 None。调用方需自行处理。
    """
    pool = _get_redis_pool()
    if pool is None:
        return None
    return Redis(connection_pool=pool)
