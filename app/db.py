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

engine = create_engine(DATABASE_URL, echo=True)
SQLModel.metadata.create_all(engine)

redis_pool = ConnectionPool.from_url(REDIS_URL)


def get_session() -> Session:
    return Session(engine)


def get_session_with():
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def get_redis():
    r = Redis(connection_pool=redis_pool)
    try:
        yield r
    finally:
        r.close()
