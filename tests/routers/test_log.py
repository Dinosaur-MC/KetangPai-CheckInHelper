"""签到日志 API 测试：清理功能（管理员专用）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_USER_EMAIL = "log-test-user@example.com"
_ADMIN_EMAIL = "log-test-admin@example.com"
_PASSWORD = "TestPass1"


@pytest.fixture
def user_token(client: TestClient) -> str:
    """注册一个普通用户并返回 access_token。"""
    resp = client.post(
        "/api/register", json={"email": _USER_EMAIL, "password": _PASSWORD}
    )
    assert resp.status_code == 200
    return resp.json()["data"]["access_token"]


@pytest.fixture
def admin_token(client: TestClient) -> str:
    """注册用户后提升为管理员，返回 access_token。"""
    resp = client.post(
        "/api/register", json={"email": _ADMIN_EMAIL, "password": _PASSWORD}
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]

    from app.core.db import get_session
    from app.models import User
    from sqlmodel import select

    with get_session() as db:
        user = db.exec(select(User).where(User.email == _ADMIN_EMAIL)).first()
        assert user is not None
        user.role = "admin"
        db.add(user)
        db.commit()

    return token


class TestCleanupLogs:
    CLEANUP_URL = "/api/logs/cleanup"

    def test_cleanup_logs_admin_only(
        self, client: TestClient, user_token: str, admin_token: str
    ):
        """普通用户不能调用清理 API，管理员可以。"""
        # 普通用户 should get 403
        resp = client.post(
            self.CLEANUP_URL,
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 403

        # 管理员 should get 200
        resp = client.post(
            self.CLEANUP_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert "expired" in data["data"]
        assert "excess" in data["data"]
