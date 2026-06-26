"""Tests for app/models.py and app/core/api.py Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.api import (
    CheckInRequest,
    CheckInResult,
    CourseItem,
    GetUserInfoResponse,
    LoginRequest,
    LoginResponse,
    QRCheckInRequest,
    _extract_gps,
    is_position_error,
)
from app.models import (
    AutoCheckinConfig,
    BaseResponse,
    ErrorResponse,
    PaginatedResponse,
    User,
    generate_invite_code,
)


# ===========================================================================
# generate_invite_code
# ===========================================================================


class TestGenerateInviteCode:
    def test_length(self):
        code = generate_invite_code()
        assert len(code) == 16

    def test_uppercase_alphanumeric(self):
        code = generate_invite_code()
        assert code.isupper()
        assert code.isalnum()

    def test_uniqueness(self):
        codes = {generate_invite_code() for _ in range(100)}
        assert len(codes) == 100


# ===========================================================================
# Pydantic response models
# ===========================================================================


class TestBaseResponse:
    def test_defaults(self):
        resp = BaseResponse()
        assert resp.code == 200
        assert resp.message == "success"
        assert resp.data is None

    def test_with_data(self):
        resp = BaseResponse(data={"key": "value"})
        assert resp.data == {"key": "value"}

    def test_serialization(self):
        resp = BaseResponse(code=400, message="bad request", data={"err": "x"})
        d = resp.model_dump(exclude_none=True)
        assert d == {"code": 400, "message": "bad request", "data": {"err": "x"}}


class TestErrorResponse:
    def test_minimal(self):
        resp = ErrorResponse(code=404, message="Not Found")
        assert resp.detail is None

    def test_with_detail(self):
        resp = ErrorResponse(code=422, message="Validation Error", detail={"field": "bad"})
        assert resp.detail == {"field": "bad"}


class TestPaginatedResponse:
    def test_construction(self):
        resp = PaginatedResponse(
            message="ok",
            data=[1, 2, 3],
            total=100,
            page=2,
            page_size=10,
        )
        assert resp.code == 200
        assert resp.total == 100
        assert resp.page == 2


# ===========================================================================
# SQLModel models
# ===========================================================================


class TestUserModel:
    def test_default_role(self):
        user = User(email="test@test.com", password="hash")
        assert user.role == "user"
        assert user.is_active is True

    def test_timestamps_on_creation(self):
        before = datetime.now(timezone.utc)
        user = User(email="a@b.com", password="hash")
        after = datetime.now(timezone.utc)
        assert before <= user.created_at <= after
        assert before <= user.last_login_at <= after

    def test_admin_role(self):
        user = User(email="admin@test.com", password="hash", role="admin")
        assert user.role == "admin"


class TestAutoCheckinConfig:
    def test_defaults(self):
        cfg = AutoCheckinConfig(user_id=1)
        assert cfg.enabled is False
        assert cfg.checkin_types == "1,2"
        assert cfg.time_windows == "[]"

    def test_timestamps(self):
        before = datetime.now(timezone.utc)
        cfg = AutoCheckinConfig(user_id=1)
        after = datetime.now(timezone.utc)
        assert before <= cfg.created_at <= after
        assert before <= cfg.updated_at <= after


# ===========================================================================
# KetangPai API models
# ===========================================================================


class TestLoginRequest:
    def test_defaults(self):
        req = LoginRequest(email="test@test.com", password="pass")
        assert req.remember == "1"
        assert req.source_type == 1


class TestLoginResponse:
    def test_parse(self):
        raw = {
            "status": 1,
            "code": 200,
            "message": "ok",
            "data": {"token": "abc", "uid": "123"},
        }
        resp = LoginResponse(**raw)
        assert resp.status == 1
        assert resp.data.token == "abc"
        assert resp.data.uid == "123"

    def test_empty_data(self):
        resp = LoginResponse(status=0, code=500, message="fail", data={"token": None, "uid": None})
        assert resp.data.token is None


class TestQRCheckInRequest:
    def test_construction(self):
        req = QRCheckInRequest(ticketid="t1", expire=12345, sign="s1", courseid="c1")
        assert req.ticketid == "t1"
        assert req.expire == 12345

    def test_extra_fields_allowed(self):
        """QRCheckInRequest has extra='allow'."""
        req = QRCheckInRequest(
            ticketid="t1", expire=100, sign="s1", courseid="c1",
            randNum="1234", type="1",
        )
        assert req.randNum == "1234"  # type: ignore[attr-defined]


class TestCheckInRequest:
    def test_defaults(self):
        req = CheckInRequest(id="att1")
        assert req.courseid == ""
        assert req.code == ""
        assert req.latitude == ""
        assert req.longitude == ""

    def test_extra_fields_allowed(self):
        req = CheckInRequest(id="att1", courseid="c1", unknown="keep")
        assert req.unknown == "keep"  # type: ignore[attr-defined]


class TestCheckInResult:
    def test_success(self):
        r = CheckInResult(email="a@b.com", success=True, message="ok")
        assert r.code == 0

    def test_failure(self):
        r = CheckInResult(email="a@b.com", success=False, message="fail", code=30319)
        assert r.code == 30319


class TestCourseItem:
    def test_alias(self):
        raw = {"id": "c1", "code": "CS101", "coursename": "Intro"}
        item = CourseItem(**raw)
        assert item.course_name == "Intro"
        assert item.id == "c1"


class TestGetUserInfoResponse:
    def test_parse_minimal(self):
        raw = {
            "status": 1,
            "code": 200,
            "message": "ok",
            "data": {
                "id": "u1",
                "username": "Alice",
                "avatar": "https://example.com/avatar.png",
                "usertype": "1",
                "stno": "2024001",
                "school": "University",
                "account": "alice",
                "mobile": "13800138000",
                "notify1": "0",
                "notify2": "0",
                "notify3": "0",
                "notify4": "0",
                "isenterprise": "0",
                "atteststate": 0,
                "isvip": 0,
                "openid": "",
                "unionid": "",
                "wechat_nikename": "",
                "additionInfo": {},
                "userScore": 0,
                "coid": 0,
                "endtime": 0,
                "i18nSwitchEnabled": 0,
            },
        }
        resp = GetUserInfoResponse(**raw)
        assert resp.data.username == "Alice"
        assert resp.data.stno == "2024001"
        assert resp.data.additionInfo is not None


# ===========================================================================
# is_position_error
# ===========================================================================


class TestIsPositionError:
    def test_success_not_position_error(self):
        r = CheckInResult(email="a@b.com", success=True, message="ok", code=0)
        assert not is_position_error(r)

    def test_code_30315(self):
        r = CheckInResult(email="a@b.com", success=False, message="fail", code=30315)
        assert is_position_error(r)

    def test_code_30320(self):
        r = CheckInResult(email="a@b.com", success=False, message="fail", code=30320)
        assert is_position_error(r)

    def test_code_30321(self):
        r = CheckInResult(email="a@b.com", success=False, message="fail", code=30321)
        assert is_position_error(r)

    def test_code_30323(self):
        r = CheckInResult(email="a@b.com", success=False, message="fail", code=30323)
        assert is_position_error(r)

    def test_keyword_position(self):
        r = CheckInResult(email="a@b.com", success=False, message="不在签到位置范围内", code=999)
        assert is_position_error(r)

    def test_keyword_range(self):
        r = CheckInResult(email="a@b.com", success=False, message="超出签到范围", code=999)
        assert is_position_error(r)

    def test_keyword_distance(self):
        r = CheckInResult(email="a@b.com", success=False, message="距离过远", code=999)
        assert is_position_error(r)

    def test_keyword_location(self):
        r = CheckInResult(email="a@b.com", success=False, message="定位失败", code=999)
        assert is_position_error(r)

    def test_failure_no_position_keywords(self):
        r = CheckInResult(email="a@b.com", success=False, message="网络错误", code=999)
        assert not is_position_error(r)

    def test_empty_message(self):
        r = CheckInResult(email="a@b.com", success=False, message="", code=500)
        assert not is_position_error(r)


# ===========================================================================
# _extract_gps
# ===========================================================================


class TestExtractGPS:
    def test_dict_lat_lng(self):
        lat, lng = _extract_gps({"lat": "30.5", "lng": "120.3"})
        assert lat == "30.5"
        assert lng == "120.3"

    def test_dict_latitude_longitude(self):
        lat, lng = _extract_gps({"latitude": "31.2", "longitude": "121.4"})
        assert lat == "31.2"
        assert lng == "121.4"

    def test_list_first_item(self):
        lat, lng = _extract_gps([{"lat": "29.8", "lng": "119.9"}, {"lat": "30.1", "lng": "120.2"}])
        assert lat == "29.8"
        assert lng == "119.9"

    def test_empty_list(self):
        lat, lng = _extract_gps([])
        assert lat is None
        assert lng is None

    def test_empty_dict(self):
        lat, lng = _extract_gps({})
        assert lat is None
        assert lng is None

    def test_none(self):
        lat, lng = _extract_gps(None)
        assert lat is None
        assert lng is None

    def test_missing_keys(self):
        lat, lng = _extract_gps({"other": "value"})
        assert lat is None
        assert lng is None

    def test_prefers_lat_over_latitude(self):
        """lat key takes priority over latitude."""
        lat, lng = _extract_gps({"lat": "10", "latitude": "20", "lng": "30"})
        assert lat == "10"
        assert lng == "30"

    def test_list_with_non_dict_item(self):
        lat, lng = _extract_gps(["string"])
        assert lat is None
        assert lng is None
