"""
补齐新字段脚本：为字段扩展前已存在的课堂派账号补充用户详情。

用法：
    uv run python scripts/backfill_accounts.py

说明：
    - 遍历所有 Account，对 username/school/stno 等字段为空的账号
      调用课堂派 getUserInfo API 获取并更新
    - 失败自动跳过，不影响现有数据
"""

import sys
import os
import time
import logging
from pathlib import Path

# 确保能找到项目根目录的 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session, select
from app.db import engine
from app.models import Account
from app.security import decrypt_credential
from app.api import KetangPaiAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")


def main():
    session = Session(engine)

    # 查询需要补齐的账号（username 为空说明是新字段扩展前的旧数据）
    stmt = select(Account).where(Account.username == "")
    accounts = session.exec(stmt).all()

    if not accounts:
        logger.info("没有需要补齐的账号")
        return

    logger.info("共 %s 个账号需要补齐", len(accounts))

    ok = 0
    fail = 0
    for acc in accounts:
        try:
            password = decrypt_credential(acc.password)
            client = KetangPaiAPI(acc.email, password)
            client.login()
            info = client.get_user_info().data
            client.close()

            acc.username = info.username
            acc.avatar = info.avatar
            acc.school = info.school
            acc.stno = info.stno
            acc.department = info.department or ""
            acc.mobile = info.mobile
            acc.ktp_account = info.account
            session.add(acc)
            session.flush()
            logger.info("✓ %s → %s / %s", acc.email, info.username, info.school)
            ok += 1
        except Exception as e:
            logger.warning("✗ %s 跳过：%s", acc.email, e)
            fail += 1

        time.sleep(0.5)  # 避免触发课堂派限流

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("完成：成功 %s 个，跳过 %s 个", ok, fail)


if __name__ == "__main__":
    main()
