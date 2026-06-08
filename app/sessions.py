from threading import Lock
from app.api import CheckInRequest, KetangPaiAPI, CheckInResult
from app.db import get_session, get_redis, Redis
from app.models import Account, CheckInLog
import asyncio

import logging

logger = logging.getLogger(__name__)

TOKEN_EXPIRE_TIME = 60 * 60 * 24 * 5


class SessionPool:
    def __init__(self):
        # 客户端池
        self.clients: dict[int, KetangPaiAPI] = {}
        # 锁
        self.lock = Lock()
        self.exec_lock = Lock()
        logger.info("会话池初始化完成")

    def create(self, accounts: list[Account]):
        with self.lock:
            r: Redis = get_redis()
            vacc = [x for x in accounts if x.id not in self.clients.keys()]
            if len(vacc) == 0:
                return False
            for account in vacc:
                if account:
                    token = r.get(f"account:{account.id}:token")
                    client = KetangPaiAPI(account.email, account.password, token)
                    if not token:
                        try:
                            client.login()
                            r.set(
                                f"account:{account.id}:token",
                                client.token,
                                TOKEN_EXPIRE_TIME,
                            )
                        except Exception:
                            continue
                    self.clients[account.id] = client
            return True

    def remove(self, account_ids: list[int]):
        with self.exec_lock:
            with self.lock:
                vid = [x for x in account_ids if x in self.clients.keys()]
                for x in vid:
                    self.clients.pop(x).close()
                return True

    def execute_checkin(
        self, user_id: int, account_ids: list[int], data: CheckInRequest
    ) -> list[tuple[int, CheckInResult]]:
        with self.exec_lock:
            with get_session() as db:
                r: Redis = get_redis()

                async def checkin(account_id: int, client: KetangPaiAPI):
                    if not client:
                        return (account_id, None)
                    result = client.check_in(data)
                    r.set(
                        f"account:{account_id}:token",
                        client.token,
                        TOKEN_EXPIRE_TIME,
                    )
                    db.add(
                        CheckInLog(
                            user_id=user_id,
                            account_id=account_id,
                            course_id=data.courseid,
                            status=1 if result.success else 0,
                        )
                    )
                    return (account_id, result)

                if len(account_ids) == 0:
                    return []

                results = []
                idx = 0
                for i in range(len(account_ids)):
                    client = self.clients.get(account_ids[i])
                    if client:
                        idx = i
                        result = client.check_in(data)
                        db.add(
                            CheckInLog(
                                user_id=user_id,
                                account_id=account_ids[i],
                                course_id=data.courseid,
                                status=1 if result.success else 0,
                            )
                        )
                        if not result.success:
                            db.commit()
                            return [(account_ids[i], result)]

                        r.set(
                            f"account:{account_ids[i]}:token",
                            client.token,
                            TOKEN_EXPIRE_TIME,
                        )
                        results.append(result)
                    else:
                        results.append((account_ids[i], None))

                tasks = []
                for i in range(idx + 1, len(account_ids)):
                    client = self.clients.get(account_ids[i])
                    tasks.append(asyncio.create_task(checkin(account_ids[i], client)))

                results.extend(asyncio.run(asyncio.gather(*tasks)))
                db.commit()
                return results


# 创建会话池
session_pool = SessionPool()
