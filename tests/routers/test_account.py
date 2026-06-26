"""账号管理端到端测试：CRUD + 关联 + 级联删除。

依赖 conftest.py 的 SQLite + Mock Redis + JWT 环境。
KetangPaiAPI.login / get_user_info 自动被 mock（无需真实凭据）。
"""

from __future__ import annotations
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_EMAIL = "acct-test@example.com"
_PASSWORD = "AcctPass1"


# ── Mock KetangPaiAPI 网络调用 ──


@pytest.fixture(autouse=True)
def _mock_ketangpai(monkeypatch: pytest.MonkeyPatch):
    """让 create_account / verify 不实际请求课堂派。"""
    from app.core import api as api_module

    def _mock_init(self, email, password, token=None):
        self.email = email
        self.password = password
        self.token = token or "mock-token"
        self.user_info = None

    monkeypatch.setattr(api_module.KetangPaiAPI, "__init__", _mock_init)

    async def _mock_login(self):
        self.token = "mock-token"
        return api_module.LoginResponse(
            status=1, code=200, message="ok",
            data=api_module.LoginData(token="mock-token", uid="mock-uid-001"),
        )

    monkeypatch.setattr(api_module.KetangPaiAPI, "login", _mock_login)

    async def _mock_user_info(self):
        return api_module.GetUserInfoResponse(
            status=1, code=200, message="ok",
            data=api_module.UserInfo(
                id="mock-uid-001", username="Mock User", avatar="https://avatar.test/1.png",
                department="CS", usertype="1", stno="2024001", school="Test University",
                account=self.email, mobile="13800138000",
                notify1="0", notify2="0", notify3="0", notify4="0",
                isenterprise="0", atteststate=0, attestInfo=[],
                isvip=0, openid="", unionid="", wechat_nikename="",
                teachcourse=[], majors=[], majorsV2=[],
                additionInfo=api_module.AdditionInfo(),
                userScore=0, coid=0, endtime=0, i18nSwitchEnabled=0,
            ),
        )

    monkeypatch.setattr(api_module.KetangPaiAPI, "get_user_info", _mock_user_info)
    monkeypatch.setattr(api_module.KetangPaiAPI, "get_course_list", AsyncMock(return_value=[]))
    monkeypatch.setattr(api_module.KetangPaiAPI, "close", AsyncMock())

    yield


# ── Fixtures ──


@pytest.fixture
def user_token(client: TestClient) -> str:
    """注册一个测试用户并返回 access_token。"""
    resp = client.post("/api/register", json={"email": _EMAIL, "password": _PASSWORD})
    assert resp.status_code == 200
    return resp.json()["data"]["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# Create + List + Get
# ===========================================================================


class TestCreateAccount:
    CREATE_URL = "/api/accounts"
    TEST_EMAIL = "new-account@test.com"

    def test_create_success(self, client: TestClient, user_token):
        resp = client.post(
            self.CREATE_URL,
            json={"email": self.TEST_EMAIL, "password": "AcctPass1"},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["data"]["email"] == self.TEST_EMAIL
        assert data["data"]["username"] == "Mock User"
        assert data["data"]["school"] == "Test University"
        assert "password" not in data["data"]  # 不返回密码

    def test_create_without_auth(self, client: TestClient):
        resp = client.post(
            self.CREATE_URL,
            json={"email": "noauth@test.com", "password": "AcctPass1"},
        )
        assert resp.status_code == 401

    def test_create_duplicate_link(self, client: TestClient, user_token):
        """同个账号重复关联到同一用户应报错。"""
        client.post(
            self.CREATE_URL,
            json={"email": self.TEST_EMAIL, "password": "AcctPass1"},
            headers=_auth_headers(user_token),
        )
        resp = client.post(
            self.CREATE_URL,
            json={"email": self.TEST_EMAIL, "password": "AcctPass1"},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 400
        assert "已关联" in resp.json()["message"]


class TestListAccounts:
    LIST_URL = "/api/accounts"

    @pytest.fixture(autouse=True)
    def _setup(self, client: TestClient, user_token):
        """创建几个测试账号。"""
        for i in range(3):
            client.post(
                "/api/accounts",
                json={"email": f"list-{i}@test.com", "password": "AcctPass1"},
                headers=_auth_headers(user_token),
            )

    def test_list(self, client: TestClient, user_token):
        resp = client.get(self.LIST_URL, headers=_auth_headers(user_token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert len(data["data"]) == 3
        assert "password" not in data["data"][0]

    def test_list_pagination(self, client: TestClient, user_token):
        resp = client.get(
            f"{self.LIST_URL}?page=1&page_size=2",
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert data["total"] == 3
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_list_requires_auth(self, client: TestClient):
        client.cookies.clear()  # 清除之前登录留下的 cookie
        resp = client.get(self.LIST_URL)
        assert resp.status_code == 401


class TestGetAccount:
    @pytest.fixture(autouse=True)
    def _setup(self, client: TestClient, user_token):
        resp = client.post(
            "/api/accounts",
            json={"email": "get-me@test.com", "password": "AcctPass1"},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        self.account_id = resp.json()["data"]["id"]

    def test_get(self, client: TestClient, user_token):
        resp = client.get(
            f"/api/accounts/{self.account_id}",
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["email"] == "get-me@test.com"

    def test_get_not_found(self, client: TestClient, user_token):
        resp = client.get(
            "/api/accounts/99999",
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 404

    def test_get_other_users_account(self, client: TestClient):
        """其他用户无法访问。"""
        # 用另一个 token
        resp = client.post(
            "/api/register",
            json={"email": "other@test.com", "password": "OtherPass1"},
        )
        other_token = resp.json()["data"]["access_token"]
        resp = client.get(
            f"/api/accounts/{self.account_id}",
            headers=_auth_headers(other_token),
        )
        assert resp.status_code == 404


# ===========================================================================
# Update
# ===========================================================================


class TestUpdateAccount:
    @pytest.fixture(autouse=True)
    def _setup(self, client: TestClient, user_token):
        resp = client.post(
            "/api/accounts",
            json={"email": "update-me@test.com", "password": "AcctPass1"},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        self.account_id = resp.json()["data"]["id"]

    def test_update_email(self, client: TestClient, user_token):
        resp = client.put(
            f"/api/accounts/{self.account_id}",
            json={"email": "updated@test.com"},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["email"] == "updated@test.com"

    def test_update_status(self, client: TestClient, user_token):
        resp = client.put(
            f"/api/accounts/{self.account_id}",
            json={"status": -1},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == -1

    def test_update_no_permission(self, client: TestClient):
        client.cookies.clear()
        resp = client.put(
            f"/api/accounts/{self.account_id}",
            json={"email": "hacker@test.com"},
        )
        assert resp.status_code == 401

    def test_update_not_found(self, client: TestClient, user_token):
        resp = client.put(
            "/api/accounts/99999",
            json={"email": "nobody@test.com"},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 404


# ===========================================================================
# Delete
# ===========================================================================


class TestDeleteAccount:
    @pytest.fixture(autouse=True)
    def _setup(self, client: TestClient, user_token):
        resp = client.post(
            "/api/accounts",
            json={"email": "delete-me@test.com", "password": "AcctPass1"},
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        self.account_id = resp.json()["data"]["id"]

    def test_delete(self, client: TestClient, user_token):
        resp = client.delete(
            f"/api/accounts/{self.account_id}",
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "删除成功"

        # 验证已删除
        resp = client.get(
            f"/api/accounts/{self.account_id}",
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 404

    def test_delete_not_found(self, client: TestClient, user_token):
        resp = client.delete(
            "/api/accounts/99999",
            headers=_auth_headers(user_token),
        )
        assert resp.status_code == 404

    def test_delete_no_auth(self, client: TestClient):
        client.cookies.clear()
        resp = client.delete(f"/api/accounts/{self.account_id}")
        assert resp.status_code == 401
