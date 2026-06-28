"""
SQLModel + MySQL + Redis
"""

import time
import threading
import logging
from sqlmodel import create_engine, SQLModel, Session, text
from redis import Redis, ConnectionPool

from app.core.settings import settings
from app.models import *
from app.core.schema_sync import SchemaSync, wait_db_ready

logger = logging.getLogger(__name__)

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL 环境变量未设置。"
        "请在 .env 文件中配置，例如：\n"
        "DATABASE_URL=mysql+pymysql://user:password@host:port/dbname?charset=utf8mb4"
    )

_engine_kwargs: dict = {"echo": settings.db_echo}
# pool_size / max_overflow / pool_recycle 是 MySQL 连接池参数，
# SQLite 的 SingletonThreadPool 不接受这些参数。仅在 MySQL 方言时传入。
if settings.database_url.startswith("mysql"):
    _engine_kwargs["pool_size"] = settings.db_pool_size
    _engine_kwargs["max_overflow"] = settings.db_max_overflow
    _engine_kwargs["pool_recycle"] = settings.db_pool_recycle

_engine = create_engine(settings.database_url, **_engine_kwargs)

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
                        settings.redis_url,
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
        - 客户端操作首次失败后立即将 *redis_available* 翻转为 ``False``，
          后续请求立即返回 ``None``。
        - 每 5min 自动尝试一次 ping 恢复检查。

    返回的客户端已是 *RedisWrapper* 包装，调用方无需重复 try/except::

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


def init_db():
    """在应用启动时调用，确保表已创建并自动同步 schema。"""
    if settings.db_auto_migrate:
        wait_db_ready(_engine)
    SQLModel.metadata.create_all(_engine)
    if settings.db_auto_migrate:
        sync = SchemaSync(
            _engine,
            backup_dir=settings.db_backup_dir,
        )
        sync.execute()


def get_session() -> Session:
    return Session(_engine)


def get_session_with():
    with Session(_engine) as session:
        try:
            yield session
            session.commit()  # commit before __exit__ calls close()
        except Exception:
            session.rollback()
            raise
