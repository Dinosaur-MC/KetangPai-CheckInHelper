"""
SQLModel + MySQL + Redis
"""

import os
import time
import threading
import logging
from sqlmodel import create_engine, SQLModel, Session, text
from redis import Redis, ConnectionPool

from app.models import *

logger = logging.getLogger(__name__)

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

# ── Redis 连接管理（断路器模式）──────────────────────────────────────────
#
# 设计要点：
#   - 正常路径不做 ping，避免额外网络往返（每请求开销 ≈1μs）
#   - check_redis_health() 每 30s 执行一次后台 ping，更新 _redis_available
#   - get_redis() / get_redis_client() 调用 check_redis_health() 获取缓存标志
#   - Redis 恢复后最多 30s 自动重新启用

_REDIS_SOCKET_TIMEOUT = 3  # 连接/读写超时（秒），避免 TCP 级卡死
_REDIS_HEALTH_INTERVAL = 60 * 5  # 健康检查最小间隔（秒）
_redis_pool: "ConnectionPool | None" = None
_redis_available: bool = False  # 断路器：首次健康检查后设为 True/False
_redis_pool_lock = threading.Lock()
_redis_health_lock = threading.Lock()
_last_health_check: float = 0.0


def _get_redis_pool() -> "ConnectionPool | None":
    """懒初始化 Redis 连接池，含短超时参数。线程安全（双重检查锁定）。"""
    global _redis_pool
    if _redis_pool is None:
        with _redis_pool_lock:
            if _redis_pool is None:
                try:
                    _redis_pool = ConnectionPool.from_url(
                        REDIS_URL,
                        protocol=2,
                        socket_connect_timeout=_REDIS_SOCKET_TIMEOUT,
                        socket_timeout=_REDIS_SOCKET_TIMEOUT,
                    )
                except Exception:
                    logger.exception("Redis 连接池创建失败，缓存与速率限制将不可用")
                    return None
    return _redis_pool


def check_redis_health() -> bool:
    """检查 Redis 可用性，更新断路器标志。线程安全，30 秒内不重复 ping。

    速率限制
        - 距上次检查 < 30s → 直接返回缓存标志（零网络开销）
        - 距上次检查 ≥ 30s → 执行一次 ping（约 1ms），更新标志

    返回值可直接用于 ``if check_redis_health():`` 判断。
    """
    global _redis_available, _last_health_check

    with _redis_health_lock:
        now = time.time()

        # 速率限制：距上次检查不足间隔 → 返回缓存标志
        if now - _last_health_check < _REDIS_HEALTH_INTERVAL:
            return _redis_available

        pool = _get_redis_pool()
        if pool is None:
            _redis_available = False
            _last_health_check = now
            return False

        r: Redis | None = None
        was_available = _redis_available
        try:
            r = Redis(connection_pool=pool)
            r.ping()
            _redis_available = True
            if not was_available:
                logger.info("Redis 连接已恢复")
        except Exception:
            _redis_available = False
            if was_available:
                logger.warning("Redis 连接异常，已熔断")
        finally:
            _last_health_check = now
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass

        return _redis_available


class _RedisWrapper:
    """Redis client 的薄包装层。

    所有操作透明委托给 Redis 客户端。若任一操作抛出异常，自动熔断
    （将 ``_redis_available`` 置为 ``False``），避免后续请求继续等待超时。
    """

    def __init__(self, client: Redis):
        self._client = client

    def __getattr__(self, name: str):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def wrapper(*args: object, **kwargs: object):
            try:
                return attr(*args, **kwargs)
            except Exception:
                global _redis_available
                _redis_available = False
                raise

        return wrapper


def get_redis():
    """FastAPI 依赖：获取 Redis 客户端。

    **正常路径（Redis 正常）**
        每 5min 窗口内第一次调用触发 ping（约 1ms），后续调用直接返回缓存标志。
        同 5min 内所有后续请求零额外网络开销。

    **熔断路径（Redis 不可用）**
        - 客户端操作首次失败后立即将 *\_redis\_available* 翻转为 ``False``，
          后续请求立即返回 ``None``。
        - 每 5min 自动尝试一次 ping 恢复检查。

    返回的客户端已是 *\_RedisWrapper* 包装，调用方无需重复 try/except::

        @router.get("/foo")
        async def foo(redis: Redis = Depends(get_redis)):
            if redis is None:
                return {"cached": False}
            val = redis.get("key")
    """
    global _redis_available

    if not check_redis_health():
        yield None
        return

    pool = _get_redis_pool()
    if pool is None:
        yield None
        return

    # 拆分为两个块：
    # - 块 A：Redis 客户端创建，异常仅限于连接失败
    # - 块 B：yield 给调用方（不捕获外部异常，否则会吞掉路由抛出的 HTTPException）
    r: Redis | None = None
    try:
        r = Redis(connection_pool=pool)
    except Exception as e:
        logger.error("获取 Redis 客户端异常: %s", e)
        _redis_available = False
        yield None
        return

    try:
        yield _RedisWrapper(r)
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                _redis_available = False


def get_redis_client() -> "Redis | None":
    """获取 Redis 客户端（非 FastAPI DI 场景）。

    内部集成断路器，操作失败自动熔断。返回 ``None`` 表示不可用::

        r = get_redis_client()
        if r is not None:
            val = r.get("key")
    """
    global _redis_available

    if not check_redis_health():
        return None

    pool = _get_redis_pool()
    if pool is None:
        return None

    try:
        r = Redis(connection_pool=pool)
        return _RedisWrapper(r)
    except Exception as e:
        logger.error("获取 Redis 客户端异常: %s", e)
        _redis_available = False
        return None


def _migrate():
    """增量迁移：为已有表添加新列（幂等，重复执行安全）。"""
    additions = {
        "account": [
            "ADD COLUMN username VARCHAR(255) NOT NULL DEFAULT ''",
            "ADD COLUMN avatar VARCHAR(512) NOT NULL DEFAULT ''",
            "ADD COLUMN school VARCHAR(255) NOT NULL DEFAULT ''",
            "ADD COLUMN stno VARCHAR(128) NOT NULL DEFAULT ''",
            "ADD COLUMN department VARCHAR(255) NOT NULL DEFAULT ''",
            "ADD COLUMN mobile VARCHAR(64) NOT NULL DEFAULT ''",
            "ADD COLUMN ktp_account VARCHAR(128) NOT NULL DEFAULT ''",
        ],
        "checkinlog": [
            "ADD COLUMN message VARCHAR(255) NOT NULL DEFAULT ''",
        ],
    }
    for table, cols in additions.items():
        for col in cols:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE {table} {col}"))
                    conn.commit()
            except Exception:
                pass  # 列已存在则静默跳过


def init_db():
    """在应用启动时调用，确保表已创建并运行增量迁移。"""
    SQLModel.metadata.create_all(engine)
    _migrate()


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
