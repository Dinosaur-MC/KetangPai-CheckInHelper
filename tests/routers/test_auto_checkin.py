"""自动签到配置端到端测试。

覆盖：config GET/PUT、status、trigger。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def user_token(client: TestClient) -> str:
    resp = client.post("/api/register", json={"email": "auto@test.com", "password": "AutoPass1"})
    assert resp.status_code == 200
    return resp.json()["data"]["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# GET /api/auto-checkin/config
# ===========================================================================


class TestGetAutoCheckinConfig:
    URL = "/api/auto-checkin/config"

    def test_default_config(self, client: TestClient, user_token):
        """未设置时返回默认值。"""
        resp = client.get(self.URL, headers=_h(user_token))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["enabled"] is False
        assert data["checkin_types"] == "1,2"
        assert data["time_windows"] == []

    def test_requires_auth(self, client: TestClient):
        client.cookies.clear()
        resp = client.get(self.URL)
        assert resp.status_code == 401


# ===========================================================================
# PUT /api/auto-checkin/config
# ===========================================================================


class TestPutAutoCheckinConfig:
    URL = "/api/auto-checkin/config"

    def test_enable(self, client: TestClient, user_token):
        resp = client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "1", "time_windows": [{"start": 8, "end": 22}]},
            headers=_h(user_token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["enabled"] is True
        assert data["checkin_types"] == "1"

    def test_disable(self, client: TestClient, user_token):
        # 先启用
        client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "1,2", "time_windows": [{"start": 8, "end": 22}]},
            headers=_h(user_token),
        )
        # 再禁用
        resp = client.put(
            self.URL,
            json={"enabled": False, "checkin_types": "1,2", "time_windows": []},
            headers=_h(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["enabled"] is False

    def test_multiple_time_windows(self, client: TestClient, user_token):
        windows = [{"start": 8, "end": 12}, {"start": 14, "end": 18}]
        resp = client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "1,2", "time_windows": windows},
            headers=_h(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["time_windows"] == windows

    def test_duplicate_windows_dedup(self, client: TestClient, user_token):
        """重复时段应自动去重。"""
        resp = client.put(
            self.URL,
            json={
                "enabled": True,
                "checkin_types": "1,2",
                "time_windows": [{"start": 8, "end": 12}, {"start": 8, "end": 12}],
            },
            headers=_h(user_token),
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]["time_windows"]) == 1

    def test_invalid_checkin_type(self, client: TestClient, user_token):
        """无效签到类型应拒绝。"""
        resp = client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "3", "time_windows": [{"start": 8, "end": 22}]},
            headers=_h(user_token),
        )
        assert resp.status_code == 422

    def test_invalid_time_window_start(self, client: TestClient, user_token):
        """start < end 校验。"""
        resp = client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "1", "time_windows": [{"start": 12, "end": 8}]},
            headers=_h(user_token),
        )
        assert resp.status_code == 422

    def test_too_many_windows(self, client: TestClient, user_token):
        """最多 16 个时段。"""
        windows = [{"start": i, "end": i + 1} for i in range(17)]
        resp = client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "1", "time_windows": windows},
            headers=_h(user_token),
        )
        assert resp.status_code == 422

    def test_checkin_types_dedup_and_sort(self, client: TestClient, user_token):
        """签到类型去重排序。"""
        resp = client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "2,1,2", "time_windows": [{"start": 8, "end": 22}]},
            headers=_h(user_token),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["checkin_types"] == "1,2"

    def test_persists_across_requests(self, client: TestClient, user_token):
        """配置修改后在 GET 中保持一致。"""
        client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "1", "time_windows": [{"start": 9, "end": 17}]},
            headers=_h(user_token),
        )
        resp = client.get(self.URL, headers=_h(user_token))
        assert resp.status_code == 200
        d = resp.json()["data"]
        assert d["enabled"] is True
        assert d["checkin_types"] == "1"
        assert d["time_windows"] == [{"start": 9, "end": 17}]

    def test_requires_auth(self, client: TestClient):
        client.cookies.clear()
        resp = client.put(
            self.URL,
            json={"enabled": True, "checkin_types": "1", "time_windows": [{"start": 8, "end": 22}]},
        )
        assert resp.status_code == 401


# ===========================================================================
# GET /api/auto-checkin/status
# ===========================================================================


class TestAutoCheckinStatus:
    URL = "/api/auto-checkin/status"

    def test_status_structure(self, client: TestClient, user_token):
        """状态返回包含必要字段。"""
        resp = client.get(self.URL, headers=_h(user_token))
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 至少包含这些字段
        assert "last_tick_time" in data
        assert "last_result" in data
        assert "current_hour" in data
        assert "user_active" in data

    def test_user_inactive_when_disabled(self, client: TestClient, user_token):
        """未启用自动签到 → user_active=False。"""
        resp = client.get(self.URL, headers=_h(user_token))
        assert resp.json()["data"]["user_active"] is False

    def test_user_active_when_configured(self, client: TestClient, user_token):
        """启用 + 配置时段 → user_active=True。"""
        client.put(
            "/api/auto-checkin/config",
            json={"enabled": True, "checkin_types": "1", "time_windows": [{"start": 0, "end": 23}]},
            headers=_h(user_token),
        )
        resp = client.get(self.URL, headers=_h(user_token))
        assert resp.json()["data"]["user_active"] is True


# ===========================================================================
# POST /api/auto-checkin/trigger
# ===========================================================================


class TestAutoCheckinTrigger:
    URL = "/api/auto-checkin/trigger"

    def test_trigger_without_config(self, client: TestClient, user_token):
        """未启用时触发应拒绝。"""
        resp = client.post(self.URL, headers=_h(user_token))
        assert resp.status_code == 400
        assert "开启" in _msg(resp)

    def test_trigger_after_enable(self, client: TestClient, user_token):
        """启用后触发应返回成功。"""
        client.put(
            "/api/auto-checkin/config",
            json={"enabled": True, "checkin_types": "1", "time_windows": [{"start": 0, "end": 23}]},
            headers=_h(user_token),
        )
        resp = client.post(self.URL, headers=_h(user_token))
        assert resp.status_code == 200

    def test_trigger_requires_auth(self, client: TestClient):
        client.cookies.clear()
        resp = client.post(self.URL)
        assert resp.status_code == 401


def _msg(resp) -> str:
    return resp.json().get("message") or resp.json().get("detail") or ""
