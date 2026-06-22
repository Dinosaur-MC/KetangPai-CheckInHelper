# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**CheckInHelper** — 自动化课堂派（ketangpai.com）批量签到 Web 应用。  
Monolithic architecture: FastAPI backend serves a Vue 3 SPA frontend with MySQL + Redis.

## Commands

```bash
# Install dependencies
uv sync

# Start dev server (auto-reload when DEBUG=true in .env)
uv run python main.py

# Generate Fernet key for credential encryption (REQUIRED)
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Backfill user details for legacy accounts
uv run python scripts/backfill_accounts.py

# Docker deployment (full stack: MySQL + Redis + App)
docker compose up -d
docker compose logs -f app
docker compose down
docker compose down -v  # also remove volumes
```

## Architecture

```
main.py                     # Entry point — loads .env, starts uvicorn
├── app/
│   ├── main.py             # FastAPI app: middleware, exception handlers, route registration
│   ├── api.py              # KetangPai third-party API client (requests-based)
│   ├── models.py           # SQLModel ORM models + Pydantic DTOs
│   ├── deps.py             # Shared FastAPI dependencies (get_current_user, user cache)
│   ├── utils.py            # RateLimiter, paginate helper, client IP detection
│   ├── core/
│   │   ├── settings.py     # Pydantic Settings — centralized config (reads .env)
│   │   ├── security.py     # Argon2 password hashing, JWT create/decode, Fernet encryption
│   │   ├── sessions.py     # SessionPool singleton — manages KetangPai login sessions
│   │   └── db.py           # SQLModel engine, Redis connection pool (breaker pattern), migration
│   ├── routers/            # ★ Domain route modules (split from monolithic main.py)
│   │   ├── auth.py         # register, login, logout, refresh
│   │   ├── user.py         # user CRUD + change-password
│   │   ├── account.py      # account CRUD + verify + cascade delete
│   │   ├── course.py       # course CRUD + course-binding CRUD
│   │   ├── checkin.py      # batch check-in execution
│   │   ├── invite_code.py  # invite code CRUD
│   │   ├── log.py          # check-in log list/detail/delete
│   │   └── settings.py     # system settings (invite-required toggle)
│   └── index.html          # Vue 3 SPA template
├── static/                 # Client-side assets (local, no CDN)
│   ├── index.js            # Vue 3 app (Composition API, MDUI 2, hash-routing)
│   ├── index.css
│   ├── mdui.css / mdui.global.js / vue.global.prod.js
│   ├── opencv.js           # OpenCV.js — WeChat QR decoding engine
│   ├── wechat_qrcode_files.js  # WeChat QR model
│   ├── zxing.min.js        # ZXing WASM fallback QR decoder
│   └── test.html           # QR decoder test page
├── scripts/                # Utility scripts
│   └── backfill_accounts.py  # Backfill user details for legacy accounts
└── docker-compose.yml      # MySQL 8 + Redis 7 + App
```

## Key Design Decisions

- **Routes split by domain**: The API surface is organized into domain router modules under `app/routers/`. When adding a new endpoint, locate the appropriate router file (`auth.py`, `account.py`, `course.py`, etc.) and add it there. Avoid adding routes to `app/main.py`.
- **Centralized config via pydantic-settings**: All configuration (DB, Redis, JWT, CORS, etc.) is defined in `app/core/settings.py` as a `Settings(BaseSettings)` class, loaded from `.env`. Never use `os.getenv()` directly.
- **SessionPool (module-level singleton)**: Manages KetangPai API sessions with 3-layer concurrency control — `threading.Lock` (clients dict), `asyncio.Lock` (batch serialization), `asyncio.Semaphore(5)` (per-batch concurrency). Sessions expire after 30 min idle; tokens cached in Redis for 5 days.
- **Canary check-in**: First account acts as canary. If it fails with code 30319/30322 (expired/ended), all remaining accounts skip immediately and the failure is cached in Redis for 1 hour.
- **Redis check-in dedup**: After a successful check-in, stores `checkin_done:{ticketid}:{account_id}` in Redis with TTL from ticket expiry to prevent duplicate API calls.
- **JWT with Refresh Token Rotation**: Access tokens default 24 hours, refresh tokens 30 days. Each refresh invalidates the old refresh token to prevent replay.
- **Rate limiting**: Redis sliding window via `RateLimiter` dependency class — login/register 5 req/min, check-in 10 req/min.
- **Credential encryption**: Fernet (AES-128-CBC + HMAC) via `CREDENTIAL_KEY` env var. **Required at startup** — app will crash if unset.
- **Login business-level check**: `login()` inspects `result.status != 1` and raises with the API error message (e.g., "password expired"), rather than only checking HTTP status.
- **Account verification**: `POST /api/accounts/{id}/verify` re-logs in to KetangPai, updates status/status_message, and refreshes stored user details. Updating password also resets status automatically.
- **Incremental migration**: `db.py:_migrate()` queries INFORMATION_SCHEMA.COLUMNS to detect missing columns and runs ALTER TABLE only for what's needed. Controlled by `DB_AUTO_MIGRATE` setting.
- **Redis circuit breaker**: `_RedisWrapper` proxy auto-fuses on any operation failure, avoiding repeated timeouts. Health check pings Redis every 5 minutes.
- **Client IP detection**: `get_client_ip()` in `utils.py` reads `X-Forwarded-For` / `X-Real-IP` headers for reverse proxy setups before falling back to `request.client.host`.
- **Client IP forwarding to KetangPai**: The `/api/checkin` endpoint extracts the client's real IP via `get_client_ip(request)` and passes it through `SessionPool.execute_checkin()` → `KetangPaiAPI.check_in()`, which adds an `X-Forward-For` header to the outbound request to Ketangpai. Defaults to empty (no header sent) when IP is unavailable.
- **Frontend**: Vue 3 SPA served as a static file from the FastAPI backend. Hash-based routing (`#/login`, `#/dashboard`, etc.). MDUI 2 Web Components for Material Design.
- **Async safety**: All synchronous HTTP calls (`client.login()`, `client.get_user_info()`) are wrapped with `await asyncio.to_thread()` inside `async def` endpoints to avoid blocking the event loop.

## Data Model

```
User ─── UserAccount ─── Account ─── CourseBinding ─── Course
                          Account ─── CheckInLog
InviteCode
SystemSetting
```

- `User`: App users with admin/user roles
- `Account`: KetangPai credentials (password encrypted via Fernet), plus `username`, `school`, `stno`, `avatar`, `mobile`, `ktp_account`, `status_message`
- `UserAccount`: Many-to-many link table (users ↔ accounts)
- `Course`: KetangPai courses (keyed by string ID from the API)
- `CourseBinding`: Links accounts to courses with `is_active` toggle
- `CheckInLog`: Per-account-per-course check-in records, with `message` field for result description

## Environment

- Python >= 3.13, MySQL 8.0, Redis 7
- Dependencies managed by `uv` (see `pyproject.toml`)
- Copy `.env.example` -> `.env`, set `JWT_SECRET` (required)
- Generate `CREDENTIAL_KEY` with the Fernet command above (REQUIRED — no plaintext fallback)
- `DATABASE_URL` must be set (no default — startup will fail if missing)
- All config is managed via `app/core/settings.py` (pydantic-settings), not via `os.getenv`
