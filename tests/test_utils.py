"""Tests for app/utils.py, app/core/sessions.py pure helpers, and app/core/watcher.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Request
from starlette.exceptions import HTTPException

from app.core.sessions import (
    SessionPool,
    _jitter_coordinates,
    _extract_radius,
)
from app.core.watcher import _in_time_windows
from app.utils import RateLimiter, get_client_ip, paginate


# ===========================================================================
# get_client_ip
# ===========================================================================


class MockRequest:
    """Minimal Request-like object for testing get_client_ip."""

    def __init__(self, headers: dict | None = None, client_host: str = "127.0.0.1"):
        self.headers = headers or {}
        self.client = MagicMock()
        self.client.host = client_host


class TestGetClientIP:
    def test_x_forwarded_for(self):
        req = MockRequest({"X-Forwarded-For": "203.0.113.1, proxy1, proxy2"})
        assert get_client_ip(req) == "203.0.113.1"

    def test_x_real_ip(self):
        req = MockRequest({"X-Real-IP": "203.0.113.2"})
        assert get_client_ip(req) == "203.0.113.2"

    def test_fallback_to_client_host(self):
        req = MockRequest(client_host="10.0.0.1")
        assert get_client_ip(req) == "10.0.0.1"

    def test_x_forwarded_for_takes_priority(self):
        req = MockRequest(
            {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"},
            client_host="127.0.0.1",
        )
        assert get_client_ip(req) == "1.2.3.4"

    def test_x_real_ip_fallback_when_no_forwarded(self):
        req = MockRequest({"X-Real-IP": "5.6.7.8"})
        assert get_client_ip(req) == "5.6.7.8"

    def test_no_client(self):
        """When request.client is None, return 'unknown'."""
        req = MockRequest()
        req.client = None
        assert get_client_ip(req) == "unknown"


# ===========================================================================
# RateLimiter
# ===========================================================================


class TestRateLimiter:
    def test_under_limit(self):
        redis_mock = MagicMock()
        redis_mock.incr.return_value = 1
        limiter = RateLimiter(times=5, seconds=60)

        async def run():
            request = MagicMock(spec=Request)
            request.url.path = "/api/test"
            request.headers = {}
            request.client.host = "127.0.0.1"
            # Should not raise
            await limiter(request, redis_mock)

        import asyncio

        asyncio.run(run())

    def test_over_limit_raises(self):
        redis_mock = MagicMock()
        redis_mock.incr.return_value = 6
        limiter = RateLimiter(times=5, seconds=60)

        async def run():
            request = MagicMock(spec=Request)
            request.url.path = "/api/test"
            request.headers = {}
            request.client.host = "127.0.0.1"
            with pytest.raises(HTTPException) as exc:
                await limiter(request, redis_mock)
            assert exc.value.status_code == 429

        import asyncio

        asyncio.run(run())

    def test_redis_error_does_not_block(self):
        redis_mock = MagicMock()
        redis_mock.incr.side_effect = Exception("Redis down")
        limiter = RateLimiter(times=5, seconds=60)

        async def run():
            request = MagicMock(spec=Request)
            request.url.path = "/api/test"
            request.headers = {}
            request.client.host = "127.0.0.1"
            # Should not raise despite Redis error
            await limiter(request, redis_mock)

        import asyncio

        asyncio.run(run())

    def test_redis_none_does_not_block(self):
        limiter = RateLimiter(times=5, seconds=60)

        async def run():
            request = MagicMock(spec=Request)
            request.url.path = "/api/test"
            request.headers = {}
            request.client.host = "127.0.0.1"
            # Should not raise even though redis is None
            await limiter(request, None)

        import asyncio

        asyncio.run(run())

    def test_sets_expire_on_first_request(self):
        redis_mock = MagicMock()
        redis_mock.incr.return_value = 1
        limiter = RateLimiter(times=5, seconds=60)

        async def run():
            request = MagicMock(spec=Request)
            request.url.path = "/api/test"
            request.headers = {}
            request.client.host = "127.0.0.1"
            await limiter(request, redis_mock)
            redis_mock.expire.assert_called_once_with("rate_limit:/api/test:127.0.0.1", 60)

        import asyncio

        asyncio.run(run())


# ===========================================================================
# paginate (requires SQLModel engine — unit test with mock)
# ===========================================================================


class TestPaginate:
    def test_page_default_fixes(self):
        """page < 1 gets coerced to 1, page_size < 1 gets DEFAULT_PAGE_SIZE."""
        # These are integration-level tests that require a real session+engine
        pass


# ===========================================================================
# _extract_radius
# ===========================================================================


class TestExtractRadius:
    def test_radius_key(self):
        assert _extract_radius({"radius": "200"}) == 200

    def test_range_key(self):
        assert _extract_radius({"range": "150"}) == 150

    def test_scope_key(self):
        assert _extract_radius({"scope": "300"}) == 300

    def test_distance_key(self):
        assert _extract_radius({"distance": "50"}) == 50

    def test_key_order_priority(self):
        """radius has top priority."""
        assert _extract_radius({"radius": "100", "range": "200"}) == 100

    def test_missing_returns_default(self):
        assert _extract_radius({"other": "value"}) == 100

    def test_invalid_value_returns_default(self):
        assert _extract_radius({"radius": "not-a-number"}) == 100

    def test_int_value(self):
        assert _extract_radius({"radius": 250}) == 250

    def test_custom_default(self):
        assert _extract_radius({}, default=500) == 500


# ===========================================================================
# _jitter_coordinates
# ===========================================================================


class TestJitterCoordinates:
    def test_radius_zero_returns_original(self):
        lat, lng = _jitter_coordinates(30.5, 120.3, 0)
        assert lat == 30.5
        assert lng == 120.3

    def test_radius_negative_returns_original(self):
        lat, lng = _jitter_coordinates(30.5, 120.3, -1)
        assert lat == 30.5
        assert lng == 120.3

    def test_positive_radius_produces_different_coords(self):
        lat, lng = _jitter_coordinates(30.5, 120.3, 100)
        assert lat != 30.5 or lng != 120.3

    def test_jitter_is_within_reasonable_bounds(self):
        """Jitter should not exceed ~0.6*radius in degrees."""
        for _ in range(50):
            lat, lng = _jitter_coordinates(30.0, 120.0, 200)
            # Max offset ~120m in lat, ~100m in lng at lat=30
            dlat = abs(lat - 30.0)
            dlng = abs(lng - 120.0)
            assert dlat < 0.005  # ~550m at equator, much less here
            assert dlng < 0.005

    def test_random_variation(self):
        outcomes = {_jitter_coordinates(30.5, 120.3, 50) for _ in range(20)}
        assert len(outcomes) > 1  # should produce multiple different coords


# ===========================================================================
# _pick_canary
# ===========================================================================


class TestPickCanary:
    def test_first_available(self):
        client_a = MagicMock()
        client_b = MagicMock()
        snapshot = {1: (client_a, 100.0), 2: (client_b, 200.0)}
        result = SessionPool._pick_canary(snapshot, [1, 2])
        assert result == (1, client_a)

    def test_prefers_order_in_account_ids(self):
        client_b = MagicMock()
        snapshot = {2: (client_b, 100.0)}
        result = SessionPool._pick_canary(snapshot, [2, 1])
        assert result == (2, client_b)

    def test_none_if_no_session(self):
        snapshot = {1: None, 2: None}
        result = SessionPool._pick_canary(snapshot, [1, 2])
        assert result is None

    def test_empty_snapshot(self):
        result = SessionPool._pick_canary({}, [1, 2])
        assert result is None

    def test_empty_account_ids(self):
        result = SessionPool._pick_canary({}, [])
        assert result is None

    def test_skips_missing_entries(self):
        client = MagicMock()
        snapshot = {2: (client, 100.0)}
        result = SessionPool._pick_canary(snapshot, [1, 2, 3])
        assert result == (2, client)


# ===========================================================================
# _resolve_client_email
# ===========================================================================


class TestResolveClientEmail:
    def test_found(self):
        client = MagicMock()
        client.email = "user@test.com"
        snapshot = {42: (client, 100.0)}
        assert SessionPool._resolve_client_email(snapshot, 42) == "user@test.com"

    def test_not_found(self):
        snapshot = {1: (MagicMock(), 100.0)}
        assert SessionPool._resolve_client_email(snapshot, 99) is None

    def test_none_entry(self):
        snapshot = {1: None}
        assert SessionPool._resolve_client_email(snapshot, 1) is None


# ===========================================================================
# _build_skip_results
# ===========================================================================


class TestBuildSkipResults:
    def test_returns_dict(self):
        msg = "跳过测试"
        results = SessionPool._build_skip_results([1, 2, 3], msg)
        assert isinstance(results, dict)
        assert len(results) == 3

    def test_all_results_have_correct_message(self):
        results = SessionPool._build_skip_results([1, 2], "skip-reason")
        for r in results.values():
            assert r.message == "skip-reason"
            assert r.success is False

    def test_email_format(self):
        results = SessionPool._build_skip_results([42], "reason")
        assert results[42].email == "account:42"

    def test_empty_list(self):
        results = SessionPool._build_skip_results([], "reason")
        assert results == {}


# ===========================================================================
# _in_time_windows (watcher)
# ===========================================================================


class TestInTimeWindows:
    def test_within_window(self):
        windows = '[{"start": 8, "end": 22}]'
        assert _in_time_windows(windows, 10) is True
        assert _in_time_windows(windows, 8) is True
        assert _in_time_windows(windows, 21) is True

    def test_outside_window(self):
        windows = '[{"start": 8, "end": 22}]'
        assert _in_time_windows(windows, 7) is False
        assert _in_time_windows(windows, 22) is False
        assert _in_time_windows(windows, 23) is False

    def test_midnight_window(self):
        windows = '[{"start": 22, "end": 6}]'
        assert _in_time_windows(windows, 22) is True
        assert _in_time_windows(windows, 23) is True
        assert _in_time_windows(windows, 0) is True
        assert _in_time_windows(windows, 5) is True
        assert _in_time_windows(windows, 6) is False  # end is exclusive
        assert _in_time_windows(windows, 21) is False

    def test_multiple_windows(self):
        windows = '[{"start": 8, "end": 12}, {"start": 14, "end": 18}]'
        assert _in_time_windows(windows, 9) is True
        assert _in_time_windows(windows, 12) is False  # end is exclusive
        assert _in_time_windows(windows, 15) is True
        assert _in_time_windows(windows, 18) is False
        assert _in_time_windows(windows, 13) is False

    def test_default_values(self):
        """When start/end are missing, default to 7 and 22."""
        # Uses default start=7, end=22
        # Actually the code uses .get("start", 7) and .get("end", 22)
        windows = '[{}]'
        assert _in_time_windows(windows, 7) is True
        assert _in_time_windows(windows, 21) is True
        assert _in_time_windows(windows, 6) is False
        assert _in_time_windows(windows, 22) is False

    def test_empty_windows(self):
        assert _in_time_windows("[]", 10) is False

    def test_invalid_json(self):
        assert _in_time_windows("not-json", 10) is False

    def test_malformed_json_structure(self):
        assert _in_time_windows('{"not": "an array"}', 10) is False

    def test_non_dict_in_array(self):
        assert _in_time_windows('["string"]', 10) is False

    def test_edge_hour_boundary(self):
        """Start hour 0, end hour 0 should never match."""
        windows = '[{"start": 0, "end": 0}]'
        assert _in_time_windows(windows, 0) is False

    def test_24h_coverage(self):
        """start=0, end=24 should cover all hours."""
        windows = '[{"start": 0, "end": 24}]'
        for h in range(24):
            assert _in_time_windows(windows, h), f"Hour {h} should be covered"
