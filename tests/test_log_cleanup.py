"""Tests for log cleanup logic."""
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, SQLModel, create_engine, select, func

from app.core.log_cleanup import cleanup_expired, cleanup_excess
from app.models import CheckInLog


def _setup_session():
    """Create in-memory SQLite session for testing."""
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    return session, engine


def _new_session(engine):
    """Create a fresh session from the same engine (cross-session persistence check)."""
    return Session(engine)


def _count_logs(session: Session) -> int:
    return session.exec(select(func.count()).select_from(CheckInLog)).one()


def test_cleanup_expired_deletes_old_logs():
    session, engine = _setup_session()
    now = datetime.now(timezone.utc)

    # 旧日志（100 天前）
    old = CheckInLog(user_id=1, account_id=1, course_id="c1",
                     created_at=now - timedelta(days=100))
    # 新日志（10 天前）
    new = CheckInLog(user_id=1, account_id=1, course_id="c1",
                     created_at=now - timedelta(days=10))

    session.add(old)
    session.add(new)
    session.commit()

    deleted = cleanup_expired(session, retention_days=90)
    assert deleted == 1
    remaining = session.exec(select(CheckInLog)).all()
    assert len(remaining) == 1
    assert remaining[0].id == new.id

    # 跨 session 持久性验证：在新 session 中重新读取，确认删除已提交
    session.commit()
    with _new_session(engine) as s2:
        count2 = s2.exec(select(func.count()).select_from(CheckInLog)).one()
        assert count2 == 1


def test_cleanup_expired_zero_when_none_expired():
    session, _ = _setup_session()
    now = datetime.now(timezone.utc)
    recent = CheckInLog(user_id=1, account_id=1, course_id="c1",
                        created_at=now - timedelta(days=1))
    session.add(recent)
    session.commit()

    deleted = cleanup_expired(session, retention_days=90)
    assert deleted == 0


def test_cleanup_excess_deletes_oldest_beyond_limit():
    session, _ = _setup_session()
    now = datetime.now(timezone.utc)

    # 同一个账号插入 6 条日志，限制 3 条 → 应删除 3 条（保留最新的 3 条）
    ids = []
    for i in range(6):
        log = CheckInLog(
            user_id=1, account_id=1, course_id="c1",
            created_at=now - timedelta(hours=i),
        )
        session.add(log)
        session.flush()
        ids.append(log.id)
    session.commit()

    # ids 按创建顺序排列（i=0 最新，i=5 最旧）
    # 保留最新的 3 条（ids[0], ids[1], ids[2]），删除最旧的 3 条（ids[3], ids[4], ids[5]）
    deleted = cleanup_excess(session, max_per_account=3)
    assert deleted == 3

    remaining = session.exec(
        select(CheckInLog).order_by(CheckInLog.created_at.desc())
    ).all()
    assert len(remaining) == 3
    retained_ids = {r.id for r in remaining}
    assert retained_ids == {ids[0], ids[1], ids[2]}, f"期望保留最新3条 {ids[:3]}, 实际 {retained_ids}"


def test_cleanup_excess_multiple_accounts():
    session, _ = _setup_session()
    now = datetime.now(timezone.utc)

    # account 1: 6 条 → 保留 3, 删除 3
    for i in range(6):
        session.add(CheckInLog(user_id=1, account_id=1, course_id="c1",
                               created_at=now - timedelta(hours=i)))
    # account 2: 4 条 → 保留 3, 删除 1
    for i in range(4):
        session.add(CheckInLog(user_id=2, account_id=2, course_id="c2",
                               created_at=now - timedelta(hours=i * 2)))
    session.commit()

    deleted = cleanup_excess(session, max_per_account=3)
    assert deleted == 4  # 3 + 1

    remaining = session.exec(select(CheckInLog)).all()
    assert len(remaining) == 6  # 3 + 3


def test_cleanup_excess_noop_when_within_limit():
    session, _ = _setup_session()
    now = datetime.now(timezone.utc)

    for i in range(3):
        session.add(CheckInLog(user_id=1, account_id=1, course_id="c1",
                               created_at=now - timedelta(hours=i)))
    session.commit()

    deleted = cleanup_excess(session, max_per_account=5)
    assert deleted == 0
    assert _count_logs(session) == 3


def test_cleanup_excess_empty_table():
    session, _ = _setup_session()
    deleted = cleanup_excess(session, max_per_account=500)
    assert deleted == 0
