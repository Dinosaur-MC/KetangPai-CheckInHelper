"""Shared test fixtures and configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Environment: set minimal settings BEFORE any app module is imported.
# SQLite in-memory for DB-dependent tests, fake Redis URL.
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite:///")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-16chars")
os.environ.setdefault("CREDENTIAL_KEY", "dGhpcyBpcyBhIHRlc3QgZmVybmV0IGtleSBiYXNlNjQtMzI=ogi=")
os.environ.setdefault("ALLOWED_ORIGINS", "")
os.environ.setdefault("PORT", "8765")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("DB_AUTO_MIGRATE", "false")


@pytest.fixture(autouse=True)
def _reset_jwt():
    """Reset JWT secret between tests so configure_jwt can be re-called safely.

    We monkeypatch the module-level globals so each test starts clean.
    """
    import app.core.security as sec

    # noinspection PyProtectedMember
    sec._JWT_SECRET = None
    sec._JWT_ALGORITHM = "HS256"
    sec._JWT_EXPIRE_HOURS = 24

    # Re-run the module-level configure_jwt from settings
    from app.core.settings import settings

    secret = settings.jwt_secret or "test-jwt-secret-16chars"
    sec.configure_jwt(secret, settings.jwt_algorithm, settings.jwt_expire_hours)

    yield


@pytest.fixture(autouse=True)
def _reset_credential_cipher():
    """Reset credential cipher so each test starts fresh."""
    import app.core.security as sec

    # noinspection PyProtectedMember
    sec._CREDENTIAL_CIPHER = None
    yield


@pytest.fixture
def fernet_key() -> str:
    """A valid Fernet key for credential tests."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------
DEFAULT_JWT_SECRET = "test-jwt-secret-16chars"


@pytest.fixture
def app():
    """Return the FastAPI application instance (no dependency overrides)."""
    from app.main import app as _app

    return _app


@pytest.fixture
def client(app, monkeypatch: pytest.MonkeyPatch) -> Generator:
    """FastAPI TestClient with SQLite in-memory DB and mocked Redis.

    DB session and Redis dependencies are overridden so tests never reach
    real MySQL / Redis.
    """
    from fastapi.testclient import TestClient

    # ── Patch DB engine to use SQLite in-memory ──
    from sqlmodel import create_engine, SQLModel
    from app.core import db as db_module
    from app.core.settings import settings

    import tempfile, pathlib

    # Use a temp file instead of :memory: so TestClient's background thread
    # and the main test thread share the same SQLite database.
    _tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    _tmp_db.close()
    _db_path = pathlib.Path(_tmp_db.name)
    test_engine = create_engine(
        f"sqlite:///{_db_path.as_posix()}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "_engine", test_engine, raising=False)

    # ── Disable auto-migration (fails on SQLite) ──
    settings.db_auto_migrate = False

    # ── Patch Redis to always return None / disabled ──
    monkeypatch.setattr(db_module, "_redis_available", False, raising=False)

    # ── Override the FastAPI dependency for get_redis ──
    async def _mock_get_redis():
        yield None

    # ── Override the FastAPI dependency for get_session_with ──
    def _mock_get_session_with():
        with db_module.Session(test_engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[db_module.get_redis] = _mock_get_redis
    app.dependency_overrides[db_module.get_session_with] = _mock_get_session_with

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()
    test_engine.dispose()
    try:
        _db_path.unlink(missing_ok=True)
    except PermissionError:
        pass  # Windows: file may still be locked


@pytest.fixture
def db_engine(client):
    """Return the in-memory SQLite engine used by the test client."""
    from app.core import db as db_module
    return db_module._engine
