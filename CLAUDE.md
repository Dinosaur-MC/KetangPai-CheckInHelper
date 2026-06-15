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

# Generate Fernet key for credential encryption
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
│   ├── main.py             # FastAPI app: routes, middleware, rate limiting, auth deps
│   │                      (ALL routes are defined here — no router modules)
│   ├── api.py              # KetangPai third-party API client (requests-based)
│   ├── models.py           # SQLModel ORM models + Pydantic DTOs
│   ├── security.py         # Argon2 password hashing, JWT create/decode, Fernet credential encryption
│   ├── sessions.py         # SessionPool singleton — manages KetangPai login sessions
│   ├── db.py               # SQLModel engine, Redis connection pool, incremental migration
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

- **All routes in `app/main.py`**: The entire API surface (~50 endpoints) lives in one file. No route modules or blueprints. When adding a new endpoint, add it to this file following the existing pattern.
- **SessionPool (module-level singleton)**: Manages KetangPai API sessions with 3-layer concurrency control — `threading.Lock` (clients dict), `asyncio.Lock` (batch serialization), `asyncio.Semaphore(5)` (per-batch concurrency). Sessions expire after 30 min idle; tokens cached in Redis for 5 days.
- **Canary check-in**: First account acts as canary. If it fails with code 30319/30322 (expired/ended), all remaining accounts skip immediately and the failure is cached in Redis for 1 hour.
- **Redis check-in dedup**: After a successful check-in, stores `checkin_done:{ticketid}:{account_id}` in Redis with TTL from ticket expiry to prevent duplicate API calls.
- **JWT with Refresh Token Rotation**: Access tokens default 7 days, refresh tokens 30 days. Each refresh invalidates the old refresh token to prevent replay.
- **Rate limiting**: Redis sliding window — login/register 5 req/min, check-in 10 req/min.
- **Credential encryption**: Fernet (AES-128-CBC + HMAC) via `CREDENTIAL_KEY` env var. Falls back to plaintext if unset.
- **Login business-level check**: `login()` inspects `result.status != 1` and raises with the API error message (e.g., "password expired"), rather than only checking HTTP status.
- **Account verification**: `POST /api/accounts/{id}/verify` re-logs in to KetangPai, updates status/status_message, and refreshes stored user details. Updating password also resets status automatically.
- **Incremental migration**: `db.py:_migrate()` queries INFORMATION_SCHEMA.COLUMNS to detect missing columns and runs ALTER TABLE only for what's needed. No manual migration scripts.
- **Client IP detection**: `_client_ip()` reads `X-Forwarded-For` / `X-Real-IP` headers for reverse proxy setups before falling back to `request.client.host`.
- **Frontend**: Vue 3 SPA served as a static file from the FastAPI backend. Hash-based routing (`#/login`, `#/dashboard`, etc.). MDUI 2 Web Components for Material Design.

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
- Generate `CREDENTIAL_KEY` with the Fernet command above
