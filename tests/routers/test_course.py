"""课程与绑定端到端测试。

依赖 conftest.py 的 SQLite + Mock Redis + JWT 环境。
"""

from __future__ import annotations
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_ketangpai(monkeypatch: pytest.MonkeyPatch):
    """让 create_account 不实际请求课堂派。"""
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
                id="mock-uid-001", username="Mock User", avatar="",
                usertype="1", stno="STNO", school="School",
                account=self.email, mobile="",
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


@pytest.fixture
def user_token(client: TestClient) -> str:
    resp = client.post("/api/register", json={"email": "course@test.com", "password": "CourseP1"})
    assert resp.status_code == 200
    return resp.json()["data"]["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_account(client, token) -> int:
    """创建测试账号并返回 account_id。"""
    resp = client.post(
        "/api/accounts",
        json={"email": "course-acct@test.com", "password": "AcctPass1"},
        headers=_h(token),
    )
    assert resp.status_code == 200
    return resp.json()["data"]["id"]


# ===========================================================================
# Course bindings
# ===========================================================================


class TestCourseBinding:
    COURSE_ID = "course-bind-001"

    @pytest.fixture(autouse=True)
    def _setup(self, client: TestClient, user_token):
        self.account_id = _create_account(client, user_token)
        self.token = user_token

    def test_create_binding(self, client: TestClient):
        resp = client.post(
            "/api/courses/bindings",
            json={"course_id": self.COURSE_ID, "account_id": self.account_id},
            headers=_h(self.token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["course_id"] == self.COURSE_ID
        assert data["account_id"] == self.account_id
        assert data["is_active"] is True

    def test_create_duplicate_binding(self, client: TestClient):
        client.post(
            "/api/courses/bindings",
            json={"course_id": self.COURSE_ID, "account_id": self.account_id},
            headers=_h(self.token),
        )
        resp = client.post(
            "/api/courses/bindings",
            json={"course_id": self.COURSE_ID, "account_id": self.account_id},
            headers=_h(self.token),
        )
        assert resp.status_code == 400
        assert "已存在" in _msg(resp)

    def test_create_binding_wrong_account(self, client: TestClient):
        """绑定不属于自己的账号应报错。"""
        resp = client.post(
            "/api/courses/bindings",
            json={"course_id": self.COURSE_ID, "account_id": 99999},
            headers=_h(self.token),
        )
        assert resp.status_code == 404

    def test_list_bindings(self, client: TestClient):
        client.post(
            "/api/courses/bindings",
            json={"course_id": self.COURSE_ID, "account_id": self.account_id},
            headers=_h(self.token),
        )
        resp = client.get("/api/courses/bindings", headers=_h(self.token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) >= 1

    def test_list_bindings_empty(self, client: TestClient):
        resp = client.get("/api/courses/bindings", headers=_h(self.token))
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 0

    def test_delete_binding(self, client: TestClient):
        create_resp = client.post(
            "/api/courses/bindings",
            json={"course_id": self.COURSE_ID, "account_id": self.account_id},
            headers=_h(self.token),
        )
        binding_id = create_resp.json()["data"]["id"]

        resp = client.delete(
            f"/api/courses/bindings/{binding_id}",
            headers=_h(self.token),
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "删除成功"

    def test_delete_binding_not_found(self, client: TestClient):
        resp = client.delete(
            "/api/courses/bindings/99999",
            headers=_h(self.token),
        )
        assert resp.status_code == 404

    def test_update_binding_active(self, client: TestClient):
        create_resp = client.post(
            "/api/courses/bindings",
            json={"course_id": self.COURSE_ID, "account_id": self.account_id},
            headers=_h(self.token),
        )
        binding_id = create_resp.json()["data"]["id"]

        resp = client.put(
            f"/api/courses/bindings/{binding_id}",
            json={"is_active": False},
            headers=_h(self.token),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_active"] is False

    def test_update_binding_not_found(self, client: TestClient):
        resp = client.put(
            "/api/courses/bindings/99999",
            json={"is_active": False},
            headers=_h(self.token),
        )
        assert resp.status_code == 404


# ===========================================================================
# Courses — list / get / delete
# ===========================================================================


class TestCourses:
    COURSE_IDS = ["course-a-001", "course-b-002"]

    @pytest.fixture(autouse=True)
    def _setup(self, client: TestClient, user_token, db_engine):
        self.token = user_token
        account_id = _create_account(client, user_token)
        # 先创建 Course 记录，再创建绑定
        from app.models import Course
        from sqlmodel import Session as SMSession

        with SMSession(db_engine) as db:
            for cid in self.COURSE_IDS:
                db.add(Course(id=cid, code=cid, course_name=f"课程{cid}", semester="2026-2027", term="1"))
            db.commit()
        for cid in self.COURSE_IDS:
            client.post(
                "/api/courses/bindings",
                json={"course_id": cid, "account_id": account_id},
                headers=_h(user_token),
            )

    def test_list_courses(self, client: TestClient):
        resp = client.get("/api/courses", headers=_h(self.token))
        assert resp.status_code == 200
        data = resp.json()
        ids = {c["id"] for c in data["data"]}
        assert "course-a-001" in ids
        assert "course-b-002" in ids

    def test_list_courses_empty(self, client: TestClient):
        """新用户无绑定时课程列表为空。"""
        client.cookies.clear()
        resp = client.post(
            "/api/register", json={"email": "empty@test.com", "password": "EmptyPass1"}
        )
        assert resp.status_code == 200
        t2 = resp.json()["data"]["access_token"]
        resp = client.get("/api/courses", headers=_h(t2))
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 0

    def test_get_course(self, client: TestClient):
        resp = client.get("/api/courses/course-a-001")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "course-a-001"

    def test_get_course_not_found(self, client: TestClient):
        resp = client.get("/api/courses/nonexistent")
        assert resp.status_code == 404


def _msg(resp) -> str:
    return resp.json().get("message") or resp.json().get("detail") or ""
