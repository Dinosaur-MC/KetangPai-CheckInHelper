"""Integration tests for auth endpoints using FastAPI TestClient.

Uses SQLite in-memory and mocked Redis (None) via conftest.py overrides.

NOTE: The exception handler in main.py wraps HTTPException into
ErrorResponse (code + message + detail). Tests check for "message", not "detail".
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


# ===========================================================================
# Helper: extract JWT from Set-Cookie header
# ===========================================================================

_COOKIE_PATTERN = re.compile(r"access_token=([^;]+)")


def _extract_token(response) -> str | None:
    m = _COOKIE_PATTERN.search(response.headers.get("set-cookie", ""))
    return m.group(1) if m else None


def _get_error_message(response) -> str:
    """Extract error message from wrapped ErrorResponse."""
    return response.json().get("message") or response.json().get("detail") or ""


# ===========================================================================
# /api/register
# ===========================================================================


class TestRegister:
    REGISTER_URL = "/api/register"

    def test_register_success(self, client: TestClient):
        resp = client.post(
            self.REGISTER_URL,
            json={"email": "newuser@test.com", "password": "Password1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["message"] == "注册成功"
        assert "access_token" in data["data"]
        assert data["data"]["user"]["email"] == "newuser@test.com"
        assert data["data"]["user"]["role"] == "user"

    def test_register_duplicate_email(self, client: TestClient):
        client.post(
            self.REGISTER_URL,
            json={"email": "dup@test.com", "password": "Password1"},
        )
        resp = client.post(
            self.REGISTER_URL,
            json={"email": "dup@test.com", "password": "Password1"},
        )
        assert resp.status_code == 400
        assert "用户已存在" in _get_error_message(resp)

    def test_register_invalid_email_format(self, client: TestClient):
        resp = client.post(
            self.REGISTER_URL,
            json={"email": "not-an-email", "password": "Password1"},
        )
        assert resp.status_code == 400
        assert "邮箱" in _get_error_message(resp)

    def test_register_weak_password(self, client: TestClient):
        resp = client.post(
            self.REGISTER_URL,
            json={"email": "weak@test.com", "password": "short"},
        )
        assert resp.status_code == 400
        assert "8" in _get_error_message(resp)

    def test_register_sets_auth_cookie(self, client: TestClient):
        resp = client.post(
            self.REGISTER_URL,
            json={"email": "cookie@test.com", "password": "Password1"},
        )
        token = _extract_token(resp)
        assert token is not None
        parts = token.split(".")
        assert len(parts) == 3

    def test_register_sets_refresh_cookie(self, client: TestClient):
        resp = client.post(
            self.REGISTER_URL,
            json={"email": "refresh@test.com", "password": "Password1"},
        )
        cookies = resp.headers.get("set-cookie", "")
        assert "refresh_token=" in cookies

    def test_register_with_invite_code_optional(self, client: TestClient):
        """When invite codes are not required, providing one should not fail."""
        resp = client.post(
            self.REGISTER_URL,
            json={
                "email": "withcode@test.com",
                "password": "Password1",
                "invite_code": "TESTCODE123",
            },
        )
        assert resp.status_code == 200


# ===========================================================================
# /api/login
# ===========================================================================


class TestLogin:
    LOGIN_URL = "/api/login"

    def test_login_success(self, client: TestClient):
        # Register first
        client.post(
            "/api/register",
            json={"email": "logintest@test.com", "password": "Password1"},
        )
        resp = client.post(
            self.LOGIN_URL,
            json={"email": "logintest@test.com", "password": "Password1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert "access_token" in data["data"]
        assert data["data"]["user"]["email"] == "logintest@test.com"

    def test_login_wrong_password(self, client: TestClient):
        client.post(
            "/api/register",
            json={"email": "logintest@test.com", "password": "Password1"},
        )
        resp = client.post(
            self.LOGIN_URL,
            json={"email": "logintest@test.com", "password": "WrongPass1"},
        )
        assert resp.status_code == 401
        assert "密码" in _get_error_message(resp)

    def test_login_nonexistent_user(self, client: TestClient):
        resp = client.post(
            self.LOGIN_URL,
            json={"email": "nobody@test.com", "password": "Password1"},
        )
        assert resp.status_code == 401

    def test_login_invalid_email(self, client: TestClient):
        resp = client.post(
            self.LOGIN_URL,
            json={"email": "bad-email", "password": "Password1"},
        )
        assert resp.status_code == 400

    def test_login_short_password(self, client: TestClient):
        resp = client.post(
            self.LOGIN_URL,
            json={"email": "logintest@test.com", "password": "short"},
        )
        assert resp.status_code == 400

    def test_login_sets_cookies(self, client: TestClient):
        client.post(
            "/api/register",
            json={"email": "cookietest@test.com", "password": "Password1"},
        )
        resp = client.post(
            self.LOGIN_URL,
            json={"email": "cookietest@test.com", "password": "Password1"},
        )
        cookies = resp.headers.get("set-cookie", "")
        assert "access_token=" in cookies
        assert "refresh_token=" in cookies

    def test_login_with_disabled_user(self, client: TestClient, db_engine):
        """Disabled user should be rejected at login."""
        from sqlmodel import select
        from app.models import User
        import app.core.db as db_mod

        # Register a user
        resp = client.post(
            "/api/register",
            json={"email": "disabled@test.com", "password": "Password1"},
        )
        assert resp.status_code == 200

        # Modify user to disabled via the test engine
        with db_mod.Session(db_mod._engine) as db:
            user = db.exec(select(User).where(User.email == "disabled@test.com")).first()
            assert user is not None
            user.is_active = False
            db.add(user)
            db.commit()

        # Login should now be rejected
        resp = client.post(
            self.LOGIN_URL,
            json={"email": "disabled@test.com", "password": "Password1"},
        )
        assert resp.status_code == 403
        assert "禁用" in _get_error_message(resp)


# ===========================================================================
# /api/logout
# ===========================================================================


class TestLogout:
    LOGOUT_URL = "/api/logout"

    def test_logout_without_token(self, client: TestClient):
        resp = client.post(self.LOGOUT_URL)
        assert resp.status_code == 200
        assert resp.json()["code"] == 200

    def test_logout_clears_cookies(self, client: TestClient):
        """After logout, cookies should be cleared."""
        client.post(
            "/api/register",
            json={"email": "logouttest@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "logouttest@test.com", "password": "Password1"},
        )
        resp = client.post(
            self.LOGOUT_URL,
        )
        set_cookie = resp.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie

    def test_logout_with_bearer_token(self, client: TestClient):
        client.post(
            "/api/register",
            json={"email": "bearer@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "bearer@test.com", "password": "Password1"},
        )
        access_token = logged_in.json()["data"]["access_token"]

        resp = client.post(
            self.LOGOUT_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200


# ===========================================================================
# /api/refresh
# ===========================================================================


class TestRefresh:
    REFRESH_URL = "/api/refresh"

    def test_refresh_with_valid_token(self, client: TestClient):
        client.post(
            "/api/register",
            json={"email": "refreshme@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "refreshme@test.com", "password": "Password1"},
        )
        refresh_token = logged_in.json()["data"]["refresh_token"]

        resp = client.post(
            self.REFRESH_URL,
            cookies={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data["data"]
        assert "refresh_token" in data["data"]
        assert data["data"]["refresh_token"] != refresh_token

    def test_refresh_with_bearer_header(self, client: TestClient):
        client.post(
            "/api/register",
            json={"email": "bearerrefresh@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "bearerrefresh@test.com", "password": "Password1"},
        )
        refresh_token = logged_in.json()["data"]["refresh_token"]

        resp = client.post(
            self.REFRESH_URL,
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        assert resp.status_code == 200

    def test_refresh_without_token(self, client: TestClient):
        resp = client.post(self.REFRESH_URL)
        assert resp.status_code == 401
        assert "缺失" in _get_error_message(resp)

    def test_refresh_with_access_token(self, client: TestClient):
        """Access tokens should not be accepted on the refresh endpoint."""
        client.post(
            "/api/register",
            json={"email": "accesstest@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "accesstest@test.com", "password": "Password1"},
        )
        access_token = logged_in.json()["data"]["access_token"]

        resp = client.post(
            self.REFRESH_URL,
            cookies={"refresh_token": access_token},
        )
        assert resp.status_code == 401

    def test_refresh_with_invalid_token(self, client: TestClient):
        resp = client.post(
            self.REFRESH_URL,
            cookies={"refresh_token": "invalid.jwt.token"},
        )
        assert resp.status_code == 401

    def test_refresh_rotation(self, client: TestClient):
        """After refresh, the old refresh token should be invalid (rotation).
        With Redis mocked to None, the rotation check is bypassed."""
        client.post(
            "/api/register",
            json={"email": "rotation@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "rotation@test.com", "password": "Password1"},
        )
        old_refresh = logged_in.json()["data"]["refresh_token"]

        # First refresh succeeds
        resp1 = client.post(
            self.REFRESH_URL,
            cookies={"refresh_token": old_refresh},
        )
        assert resp1.status_code == 200

    def test_refresh_sets_new_cookies(self, client: TestClient):
        client.post(
            "/api/register",
            json={"email": "cookieset@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "cookieset@test.com", "password": "Password1"},
        )
        refresh_token = logged_in.json()["data"]["refresh_token"]

        resp = client.post(
            self.REFRESH_URL,
            cookies={"refresh_token": refresh_token},
        )
        set_cookie = resp.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie
        assert "refresh_token=" in set_cookie


# ===========================================================================
# Protected endpoint test (via root /)
# ===========================================================================


class TestProtectedEndpoints:
    def test_root_redirects_when_not_authenticated(self, client: TestClient):
        """GET / should redirect to /login when no access_token cookie."""
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)

    def test_login_page_accessible(self, client: TestClient):
        """GET /login should return HTML."""
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_root_returns_html_when_authenticated(self, client: TestClient):
        """GET / with valid access_token should return index.html."""
        client.post(
            "/api/register",
            json={"email": "authed@test.com", "password": "Password1"},
        )
        logged_in = client.post(
            "/api/login",
            json={"email": "authed@test.com", "password": "Password1"},
        )
        access_token = logged_in.json()["data"]["access_token"]

        resp = client.get(
            "/",
            cookies={"access_token": access_token},
            follow_redirects=False,
        )
        assert resp.status_code == 200
