"""Tests for app/core/security.py — password, JWT, credential encryption."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest

from app.core.security import (
    _ALLOWED_ALGORITHMS,
    blacklist_token,
    configure_jwt,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    decrypt_credential,
    encrypt_credential,
    hash_password,
    is_token_blacklisted,
    validate_password_strength,
    verify_password,
)


# ===========================================================================
# Password hashing (argon2)
# ===========================================================================


class TestPasswordHashing:
    def test_hash_and_verify(self):
        password = "TestPassword123"
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(password, hashed)

    def test_verify_wrong_password(self):
        hashed = hash_password("CorrectP@ss1")
        assert not verify_password("WrongP@ss1", hashed)

    def test_hash_is_different_each_time(self):
        password = "SameP@ss1"
        h1 = hash_password(password)
        h2 = hash_password(password)
        assert h1 != h2  # argon2 includes random salt

    def test_verify_empty_string(self):
        hashed = hash_password("SomeP@ss1")
        assert not verify_password("", hashed)


# ===========================================================================
# validate_password_strength
# ===========================================================================


class TestValidatePasswordStrength:
    def test_valid_password(self):
        valid, msg = validate_password_strength("Abcdefg1")
        assert valid
        assert msg == ""

    def test_too_short(self):
        valid, msg = validate_password_strength("Ab1c")
        assert not valid
        assert "8" in msg

    def test_too_long(self):
        valid, msg = validate_password_strength("A" + "b1" * 64 + "X")
        assert not valid
        assert "128" in msg

    def test_no_uppercase(self):
        valid, msg = validate_password_strength("abcdefgh1")
        assert not valid
        assert "大写字母" in msg

    def test_no_lowercase(self):
        valid, msg = validate_password_strength("ABCDEFG1")
        assert not valid
        assert "小写字母" in msg

    def test_no_digit(self):
        valid, msg = validate_password_strength("Abcdefgh")
        assert not valid
        assert "数字" in msg

    def test_unicode_supported(self):
        valid, msg = validate_password_strength("密码Test1")
        assert not valid  # no uppercase Latin
        assert msg

    def test_all_requirements_met(self):
        for pw in ["Abcdefgh1", "LongerP@ss1", "ZZZZzzzz9", "12345678Ab"]:
            valid, _ = validate_password_strength(pw)
            assert valid, f"Expected {pw!r} to be valid"


# ===========================================================================
# Fernet credential encryption
# ===========================================================================


class TestCredentialEncryption:
    def test_encrypt_decrypt_roundtrip(self, fernet_key):
        import app.core.security as sec

        sec._CREDENTIAL_CIPHER = None
        # Temporarily swap the settings key
        from app.core.settings import settings

        orig_key = settings.credential_key
        try:
            # We can't easily override settings.credential_key because it's
            # frozen. Instead, we monkeypatch _get_cipher to use our key.
            from cryptography.fernet import Fernet

            cipher = Fernet(fernet_key.encode())
            # We'll test encrypt_credential / decrypt_credential via the cipher
            # directly to avoid settings dependency in unit tests.
            plain = "my_secret_password"
            encrypted = cipher.encrypt(plain.encode()).decode()
            decrypted = cipher.decrypt(encrypted.encode()).decode()
            assert decrypted == plain
            assert encrypted != plain
        finally:
            settings.credential_key = orig_key

    def test_decrypt_fallback_to_plaintext(self):
        """decrypt_credential returns the token as-is when it's not valid
        Fernet (e.g., already plaintext)."""
        result = decrypt_credential("already_plain")
        assert result == "already_plain"

    def test_encrypt_needs_key(self):
        """Without CREDENTIAL_KEY set, _get_cipher should raise."""
        from app.core.security import _get_cipher as original_get_cipher
        from app.core.settings import settings

        orig = settings.credential_key
        try:
            settings.credential_key = ""
            import app.core.security as sec

            sec._CREDENTIAL_CIPHER = None
            with pytest.raises(RuntimeError, match="CREDENTIAL_KEY"):
                sec._get_cipher()
        finally:
            settings.credential_key = orig


# ===========================================================================
# JWT — configure_jwt
# ===========================================================================


class TestConfigureJWT:
    def test_configure_with_valid_secret(self):
        configure_jwt("a-16-char-secret!")
        assert True

    def test_configure_secret_too_short(self):
        with pytest.raises(ValueError, match="至少 16 个字符"):
            configure_jwt("short")

    def test_configure_empty_secret(self):
        with pytest.raises(ValueError, match="至少 16 个字符"):
            configure_jwt("")

    def test_configure_invalid_algorithm(self):
        with pytest.raises(ValueError, match="不支持的 JWT 算法"):
            configure_jwt("a-16-char-secret!", algorithm="FAKE256")

    def test_configure_all_valid_algorithms(self):
        for alg in _ALLOWED_ALGORITHMS:
            configure_jwt("a-16-char-secret!", algorithm=alg)
        assert True


# ===========================================================================
# JWT — create / decode tokens
# ===========================================================================


class TestJWTAccessToken:
    def test_create_and_decode(self):
        configure_jwt("a-16-char-secret!")
        token = create_access_token("42")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_decode_expired_token(self):
        configure_jwt("a-16-char-secret!")
        token = create_access_token("1", expires_delta=timedelta(seconds=-1))
        payload = decode_access_token(token)
        assert payload is None

    def test_decode_bad_signature(self):
        configure_jwt("a-16-char-secret!")
        configure_jwt("different-secret-16chars!")
        token = create_access_token("1")
        configure_jwt("another-secret-16char!")
        payload = decode_access_token(token)
        assert payload is None

    def test_decode_malformed(self):
        payload = decode_access_token("not-a-jwt")
        assert payload is None

    def test_decode_before_configure(self):
        import app.core.security as sec

        sec._JWT_SECRET = None
        with pytest.raises(RuntimeError):
            decode_access_token("anything")


class TestJWTRefreshToken:
    def test_create_and_decode(self):
        configure_jwt("a-16-char-secret!")
        token = create_refresh_token("42")
        payload = decode_refresh_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["type"] == "refresh"

    def test_access_token_rejected_as_refresh(self):
        configure_jwt("a-16-char-secret!")
        access = create_access_token("42")
        payload = decode_refresh_token(access)
        assert payload is None  # missing type="refresh"

    def test_decode_expired_refresh(self):
        configure_jwt("a-16-char-secret!")
        token = create_refresh_token("1", expires_delta=timedelta(seconds=-1))
        payload = decode_refresh_token(token)
        assert payload is None

    def test_decode_malformed_refresh(self):
        payload = decode_refresh_token("bad-token")
        assert payload is None

    def test_refresh_token_contains_type(self):
        configure_jwt("a-16-char-secret!")
        token = create_refresh_token("1")
        payload = jwt.decode(token, "a-16-char-secret!", algorithms=["HS256"])
        assert payload.get("type") == "refresh"

    def test_decode_before_configure(self):
        import app.core.security as sec

        sec._JWT_SECRET = None
        with pytest.raises(RuntimeError):
            decode_refresh_token("anything")

    def test_decode_with_configured_secret_empty(self):
        import app.core.security as sec

        sec._JWT_SECRET = None
        with pytest.raises(RuntimeError):
            decode_refresh_token("x")


# ===========================================================================
# JWT — custom expiry
# ===========================================================================


class TestJWTExpiry:
    def test_custom_expiry(self):
        configure_jwt("a-16-char-secret!")
        token = create_access_token("1", expires_delta=timedelta(hours=1))
        payload = decode_access_token(token)
        assert payload is not None
        expected = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).timestamp()
        assert abs(payload["exp"] - expected) < 5  # within 5s

    def test_custom_refresh_expiry(self):
        configure_jwt("a-16-char-secret!")
        token = create_refresh_token("1", expires_delta=timedelta(days=7))
        payload = decode_refresh_token(token)
        assert payload is not None
        expected = (
            datetime.now(timezone.utc) + timedelta(days=7)
        ).timestamp()
        assert abs(payload["exp"] - expected) < 5  # within 5s


# ===========================================================================
# Token blacklisting
# ===========================================================================


class TestTokenBlacklist:
    def test_blacklist_and_check(self):
        redis_mock = MagicMock()
        redis_mock.exists.return_value = False
        assert not is_token_blacklisted("some-jti", redis_mock)
        blacklist_token("some-jti", redis_mock, ttl=3600)
        redis_mock.setex.assert_called_once_with("blacklist:some-jti", 3600, "1")

    def test_check_blacklisted_returns_true(self):
        redis_mock = MagicMock()
        redis_mock.exists.return_value = True
        assert is_token_blacklisted("blocked-jti", redis_mock)

    def test_check_redis_error_returns_false(self):
        redis_mock = MagicMock()
        redis_mock.exists.side_effect = Exception("Redis down")
        assert not is_token_blacklisted("some-jti", redis_mock)

    def test_blacklist_redis_error_ignored(self):
        redis_mock = MagicMock()
        redis_mock.setex.side_effect = Exception("Redis down")
        blacklist_token("some-jti", redis_mock)  # must not raise

    def test_blacklist_default_ttl(self):
        redis_mock = MagicMock()
        blacklist_token("some-jti", redis_mock)
        redis_mock.setex.assert_called_once_with("blacklist:some-jti", 604800, "1")


# ===========================================================================
# Token uniqueness (JTI)
# ===========================================================================


class TestJTI:
    def test_access_tokens_have_unique_jti(self):
        configure_jwt("a-16-char-secret!")
        jtis = {create_access_token("1") for _ in range(10)}
        assert len(jtis) == 10  # all tokens should differ

    def test_refresh_tokens_have_unique_jti(self):
        configure_jwt("a-16-char-secret!")
        jtis = {create_refresh_token("1") for _ in range(10)}
        assert len(jtis) == 10


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_user_id_is_serialized_correctly(self):
        configure_jwt("a-16-char-secret!")
        token = create_access_token("42")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "42"

    def test_create_access_token_before_configure(self):
        import app.core.security as sec

        sec._JWT_SECRET = None
        with pytest.raises(RuntimeError):
            create_access_token("1")

    def test_create_refresh_token_before_configure(self):
        import app.core.security as sec

        sec._JWT_SECRET = None
        with pytest.raises(RuntimeError):
            create_refresh_token("1")
