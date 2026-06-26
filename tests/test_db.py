"""Tests for app/core/db.py — Redis circuit breaker, health check, engine setup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.db import _RedisWrapper


# ===========================================================================
# _RedisWrapper — circuit breaker
# ===========================================================================


class TestRedisWrapper:
    def test_delegates_get(self):
        client = MagicMock()
        client.get.return_value = b"value"
        wrapper = _RedisWrapper(client)
        assert wrapper.get("key") == b"value"
        client.get.assert_called_once_with("key")

    def test_delegates_set(self):
        client = MagicMock()
        wrapper = _RedisWrapper(client)
        wrapper.set("key", "value")
        client.set.assert_called_once_with("key", "value")

    def test_delegates_delete(self):
        client = MagicMock()
        wrapper = _RedisWrapper(client)
        wrapper.delete("key")
        client.delete.assert_called_once_with("key")

    def test_delegates_exists(self):
        client = MagicMock()
        client.exists.return_value = 1
        wrapper = _RedisWrapper(client)
        assert wrapper.exists("key") == 1

    def test_circuit_breaker_on_exception(self):
        """When an operation raises, _redis_available should be set to False."""
        client = MagicMock()
        client.get.side_effect = Exception("Connection refused")
        wrapper = _RedisWrapper(client)

        # Import to check/reset the global flag
        from app.core import db as db_module

        db_module._redis_available = True

        with pytest.raises(Exception, match="Connection refused"):
            wrapper.get("key")

        assert db_module._redis_available is False

    def test_non_callable_attributes(self):
        """Non-callable attributes should be passed through as-is."""
        client = MagicMock()
        client.some_property = "hello"
        wrapper = _RedisWrapper(client)
        assert wrapper.some_property == "hello"

    def test_ping_delegation(self):
        client = MagicMock()
        wrapper = _RedisWrapper(client)
        wrapper.ping()
        client.ping.assert_called_once()


# ===========================================================================
# check_redis_health (unit tests with mocked pool/ping)
# ===========================================================================


class TestCheckRedisHealth:
    @patch("app.core.db._get_redis_pool")
    @patch("app.core.db.Redis")
    def test_health_check_success(self, mock_redis_class, mock_get_pool):
        from app.core import db as db_module

        mock_redis_instance = MagicMock()
        mock_redis_class.return_value = mock_redis_instance

        db_module._redis_available = False
        db_module._last_health_check = 0.0
        # Override the health check interval to ensure we actually ping
        import app.core.db as db_mod

        orig = db_mod._REDIS_HEALTH_INTERVAL
        db_mod._REDIS_HEALTH_INTERVAL = 0  # always check
        try:
            result = db_module.check_redis_health()
            assert result is True
            mock_redis_instance.ping.assert_called_once()
        finally:
            db_mod._REDIS_HEALTH_INTERVAL = orig

    @patch("app.core.db._get_redis_pool")
    @patch("app.core.db.Redis")
    def test_health_check_failure(self, mock_redis_class, mock_get_pool):
        from app.core import db as db_module

        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.side_effect = Exception("Connection failed")
        mock_redis_class.return_value = mock_redis_instance

        db_module._redis_available = False
        db_module._last_health_check = 0.0
        import app.core.db as db_mod

        orig = db_mod._REDIS_HEALTH_INTERVAL
        db_mod._REDIS_HEALTH_INTERVAL = 0
        try:
            result = db_module.check_redis_health()
            assert result is False
        finally:
            db_mod._REDIS_HEALTH_INTERVAL = orig

    def test_returns_cached_flag_when_within_interval(self):
        from app.core import db as db_module

        db_module._redis_available = True
        db_module._last_health_check = 9999999999.0  # far in the future

        result = db_module.check_redis_health()
        assert result is True  # cached flag returned

    @patch("app.core.db._get_redis_pool")
    def test_no_pool_returns_false(self, mock_get_pool):
        from app.core import db as db_module

        mock_get_pool.return_value = None
        db_module._redis_available = True
        db_module._last_health_check = 0.0
        import app.core.db as db_mod

        orig = db_mod._REDIS_HEALTH_INTERVAL
        db_mod._REDIS_HEALTH_INTERVAL = 0
        try:
            result = db_module.check_redis_health()
            assert result is False
        finally:
            db_mod._REDIS_HEALTH_INTERVAL = orig


# ===========================================================================
# get_session / get_session_with (structural check)
# ===========================================================================


class TestSession:
    def test_get_session_is_callable(self):
        from app.core.db import get_session

        session = get_session()
        assert session is not None

    def test_get_session_with_is_generator(self):
        from app.core.db import get_session_with

        gen = get_session_with()
        assert hasattr(gen, "__next__")
        # Cleanly close the generator
        gen.close()
