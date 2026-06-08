"""Security utilities — password hashing (argon2) and JWT tokens.

Dependencies: passlib[argon2], pyjwt
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet
from passlib.context import CryptContext
from redis import Redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password hashing — argon2 via passlib
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# JWT secret — MUST be set via configure_jwt() at startup.
# No default: any call before configure_jwt will raise.
_JWT_SECRET: str | None = None
_JWT_ALGORITHM: str = "HS256"
_JWT_EXPIRE_HOURS: int = 24

# Allowed JWT algorithms
_ALLOWED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its argon2 hash."""
    return _pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# Credential encryption — Fernet (AES-128-CBC + HMAC)
# ---------------------------------------------------------------------------

_CREDENTIAL_CIPHER: Fernet | None = None


def _get_cipher() -> Fernet:
    global _CREDENTIAL_CIPHER
    if _CREDENTIAL_CIPHER is None:
        raw_key = os.environ.get("CREDENTIAL_KEY")
        if raw_key:
            try:
                key = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
                _CREDENTIAL_CIPHER = Fernet(key)
            except Exception:
                raise ValueError("CREDENTIAL_KEY 不是有效的 Fernet 密钥（需 32 字节 base64）")
        else:
            logger.warning(
                "CREDENTIAL_KEY 未设置，课堂派凭据将明文存储。"
                "可通过 `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` 生成密钥。"
            )
    return _CREDENTIAL_CIPHER


def encrypt_credential(plaintext: str) -> str:
    """加密课堂派凭据。若未配置密钥则明文返回。"""
    cipher = _get_cipher()
    if cipher is None:
        return plaintext
    return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_credential(token: str) -> str:
    """解密课堂派凭据。若未配置密钥则假定明文。"""
    cipher = _get_cipher()
    if cipher is None:
        return token
    try:
        return cipher.decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return token  # fallback: assume already plaintext


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(
    user_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token for *user_id*.

    Args:
        user_id: The user's id.
        expires_delta: Custom expiry; defaults to 24 hours.

    Returns:
        Encoded JWT string.
    """
    if _JWT_SECRET is None:
        raise RuntimeError("JWT secret 未配置 — 请调用 configure_jwt()")
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=_JWT_EXPIRE_HOURS)
    )
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password strength. Returns (is_valid, error_message).

    Requirements: 8-128 chars, at least one uppercase, one lowercase, one digit.
    """
    if len(password) < 8:
        return False, "密码长度至少为 8 个字符"
    if len(password) > 128:
        return False, "密码长度不能超过 128 个字符"
    if not any(c.isupper() for c in password):
        return False, "密码需包含至少一个大写字母"
    if not any(c.islower() for c in password):
        return False, "密码需包含至少一个小写字母"
    if not any(c.isdigit() for c in password):
        return False, "密码需包含至少一个数字"
    return True, ""


def is_token_blacklisted(jti: str, redis: Redis) -> bool:
    """Check if a token JTI is blacklisted."""
    try:
        return redis.exists(f"blacklist:{jti}")
    except Exception:
        return False  # If Redis is down, allow the request


def blacklist_token(jti: str, redis: Redis, ttl: int = 604800) -> None:
    """Add a token JTI to the blacklist."""
    try:
        redis.setex(f"blacklist:{jti}", ttl, "1")
    except Exception:
        pass


def decode_access_token(token: str) -> dict | None:
    """Decode and validate a JWT access token.

    Returns:
        The token payload as a dict, or ``None`` if the token is
        expired / invalid.
    """
    if _JWT_SECRET is None:
        raise RuntimeError("JWT secret 未配置 — 请调用 configure_jwt()")
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired.")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT token: %s", exc)
        return None


def configure_jwt(
    secret: str,
    algorithm: str = "HS256",
    expire_hours: int = 24 * 7,
) -> None:
    """Override JWT defaults (call once at startup)."""
    global _JWT_SECRET, _JWT_ALGORITHM, _JWT_EXPIRE_HOURS
    if not secret or len(secret) < 16:
        raise ValueError("JWT_SECRET 必须至少 16 个字符")
    if algorithm not in _ALLOWED_ALGORITHMS:
        raise ValueError(f"不支持的 JWT 算法：{algorithm}. 允许值：{_ALLOWED_ALGORITHMS}")
    _JWT_SECRET = secret
    _JWT_ALGORITHM = algorithm
    _JWT_EXPIRE_HOURS = expire_hours
