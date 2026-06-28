"""签到日志清理：过期清理 + 每账号数量限制。"""

import logging
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, text

logger = logging.getLogger(__name__)


def cleanup_expired(session: Session, retention_days: int = 90) -> int:
    """删除超过 retention_days 天的签到日志。

    Args:
        session: SQLModel Session
        retention_days: 保留天数，默认 90

    Returns:
        删除的记录数
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = session.exec(
        text("DELETE FROM checkinlog WHERE created_at < :cutoff"),
        params={"cutoff": cutoff},
    )
    deleted = result.rowcount
    if deleted:
        logger.info("清理过期签到日志: 删除 %s 条 (cutoff=%s)", deleted, cutoff.isoformat())
    return deleted


def cleanup_excess(session: Session, max_per_account: int = 500) -> int:
    """删除每账号超出 max_per_account 条数的签到日志（保留最新 N 条）。

    使用 MySQL 8+ 窗口函数 ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)
    在 SQLite 3.25+ 中同样支持。

    Args:
        session: SQLModel Session
        max_per_account: 每账号最大日志数，默认 500

    Returns:
        删除的记录数
    """
    stmt = text("""
        DELETE FROM checkinlog WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY account_id ORDER BY created_at DESC
                ) AS rn FROM checkinlog
            ) AS ranked WHERE rn > :max_per_account
        )
    """)
    result = session.exec(stmt, params={"max_per_account": max_per_account})
    deleted = result.rowcount
    if deleted:
        logger.info("清理超限签到日志: 删除 %s 条 (max_per_account=%s)", deleted, max_per_account)
    return deleted


def run_cleanup(session: Session, retention_days: int = 90, max_per_account: int = 500) -> dict:
    """执行完整的签到日志清理（过期 + 超限）。

    Returns:
        {"expired": int, "excess": int} 分别表示删除条数
    """
    expired = cleanup_expired(session, retention_days)
    excess = cleanup_excess(session, max_per_account)
    result = {"expired": expired, "excess": excess}
    if expired or excess:
        logger.info("签到日志清理完成: 过期删除 %s 条, 超限删除 %s 条", expired, excess)
    return result
