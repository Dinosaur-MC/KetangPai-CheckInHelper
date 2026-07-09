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
# 启动时自动运行 SchemaSync 同步数据库结构
uv run python main.py

# Generate Fernet key for credential encryption (REQUIRED)
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Backfill user details for legacy accounts
uv run python scripts/backfill_accounts.py

# Run all tests
uv run pytest
uv run pytest -v                 # verbose
uv run pytest --tb=long          # full traceback on failures
uv run pytest tests/routers/ -v  # only router integration tests

# Benchmark tests run automatically — results saved to:
uv run pytest -v -s tests/routers/test_benchmark_checkin.py  # detailed timing
#   tests/routers/.benchmark_results.json (JSON history, .gitignored)
#   Timing: 10 warmup + 10 measure + drop 1 worst outlier, median < 50ms

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
│   ├── models.py           # SQLModel ORM models + Pydantic DTOs
│   ├── deps.py             # Shared FastAPI dependencies (get_current_user, user cache)
│   ├── utils.py            # RateLimiter, paginate helper, client IP detection
│   ├── core/
│   │   ├── api.py          # KetangPai third-party API client (httpx async)
│   │   ├── settings.py     # Pydantic Settings — centralized config (reads .env)
│   │   ├── security.py     # Argon2 password hashing, JWT create/decode, Fernet encryption
│   │   ├── sessions.py     # SessionPool singleton — manages KetangPai login sessions
│   │   ├── watcher.py      # AutoCheckinWatcher — 后台自动签到观察器（轮询 + 执行）
│   │   ├── log_cleanup.py  # LogCleanup — 签到日志过期清理和超限清理
│   │   ├── schema_sync.py  # SchemaSync — 全自动 schema 同步引擎（diff/backup/DDL/审计）
│   │   └── db.py           # SQLModel engine, Redis connection pool (breaker pattern), init_db (→ SchemaSync)
│   ├── routers/            # ★ Domain route modules (split from monolithic main.py)
│   │   ├── auth.py         # register, login, logout, refresh
│   │   ├── user.py         # user CRUD + change-password
│   │   ├── account.py      # account CRUD + verify + cascade delete
│   │   ├── course.py       # course CRUD + course-binding CRUD
│   │   ├── checkin.py      # batch check-in execution + GPS / 数字码签到
│   │   ├── invite_code.py  # invite code CRUD
│   │   ├── location.py     # CourseLocation CRUD（课程签到位置管理）
│   │   ├── log.py          # check-in log list/detail/delete
│   │   └── settings.py     # system settings (invite-required toggle)
│   ├── index.html          # Vue 3 SPA template（已登录）
│   └── login.html          # 独立登录/注册页面
├── static/                 # Client-side assets (local, no CDN)
│   ├── icons/              # PWA icons + screenshots
│   ├── common.css          # 公共样式（全局重置、表单字段、密码切换）
│   ├── login.js            # 登录/注册 Vue 应用
│   ├── login.css           # 登录页专用样式
│   ├── index.js            # Vue 3 主应用 (Composition API, MDUI 2, hash-routing)
│   ├── index.css           # 主应用样式（侧栏、表格、签到、扫码弹窗）
│   ├── mdui.css / mdui.global.js / vue.global.prod.js
│   ├── img(32).webp        # 背景图（主页）
│   ├── img(64).webp        # 背景图（登录页）
│   ├── opencv.js           # OpenCV.js — WeChat QR decoding engine
│   ├── wechat_qrcode_files.js  # WeChat QR model
│   ├── zxing.min.js        # ZXing WASM fallback QR decoder
│   └── test.html           # QR decoder test page
├── sw.js                   # PWA Service Worker
├── manifest.json           # PWA Web App Manifest
├── scripts/                # Utility scripts
│   └── backfill_accounts.py  # Backfill user details for legacy accounts
├── docker-compose.yml      # MySQL 8 + Redis 7 + App
└── pyproject.toml           # 依赖管理 + pytest 配置
```

## Key Design Decisions

- **Routes split by domain**: API surface organized into domain router modules under `app/routers/`. Add new endpoints to the appropriate router, not to `app/main.py`.
- **Centralized config via pydantic-settings**: All config in `app/core/settings.py` loaded from `.env`. Never use `os.getenv()` directly.
- **Fully async architecture**: All KetangPai API calls use `httpx.AsyncClient`. SessionPool methods are all async. No `asyncio.to_thread` wrappers needed.
- **SessionPool (module-level singleton)**: Manages KetangPai API sessions with multi-layer concurrency control. Sessions expire after 30 min idle; tokens cached in Redis for 5 days.
- **Canary check-in (QR + GPS)**: First account tested first. If it fails (expired/ended), remaining accounts skip immediately and the failure is cached in Redis.
- **Redis check-in dedup**: Prevents duplicate API calls via checkin_done keys with appropriate TTLs.
- **JWT with httponly cookies**: Access tokens (default 24h) and refresh tokens (default 30d) in httponly, SameSite=Lax cookies. `refresh_token` cookie path=`/api/refresh` (only sent to refresh endpoint). Expirations configurable via `JWT_EXPIRE_HOURS` / `JWT_REFRESH_EXPIRE_DAYS`.
- **Refresh Token Rotation**: Each refresh invalidates the old token (`refresh_used:{jti}` in Redis). Frontend uses Semaphore to ensure only one concurrent `POST /api/refresh` on parallel 401s.
- **Rate limiting**: Redis sliding window — login/register 5 req/min, check-in 10 req/min.
- **Credential encryption**: Fernet (AES-128-CBC + HMAC) via `CREDENTIAL_KEY`. **Required at startup**.
- **SchemaSync (`app/core/schema_sync.py`)**: 启动时自动从 SQLModel 模型同步数据库结构（diff → 备份 → DDL）。支持 `UniqueConstraint`、索引、外键的增量 diff。快速路径：模型哈希未变则跳过。幂等执行，迁移锁防多实例并发。
- **Course sync (`POST /api/accounts/{account_id}/sync-courses`)**: 逐账号增量同步课堂派课程列表到本地。自动创建 Course 记录和 CourseBinding（默认不启用）。幂等设计，内置 IntegrityError 处理防并发竞态。
- **IntegrityError 防御**: `create_account` 中捕获 `SAIntegrityError` 处理并发重复创建账号的竞态（回滚 → 重查 → 关联）。`sync_account_courses` 中类似保护并发课程创建。
- **LogCleanup (`app/core/log_cleanup.py`)**: 签到日志自动清理——过期清理（默认 90 天）和超限清理（每账号默认 500 条）。后台每日自动执行，也可手动触发 `POST /api/logs/cleanup`。
- **Redis circuit breaker**: `_RedisWrapper` auto-fuses on failure, avoiding repeated timeouts. Health check pings Redis every 5 minutes.
- **Client IP forwarding to KetangPai**: Check-in requests forward client real IP via `X-Forward-For` header to avoid all showing the same server IP.
- **Frontend**: Two-page architecture — `login.html` (login/register) and `index.html` (main SPA). Root route serves SPA unconditionally; auth handled by frontend (401 → refresh → retry, fallback to `/login`).
- **PWA**: Installable via `manifest.json` + Service Worker. Three-tier caching: network-first for app code (HTML/JS/CSS), cache-first for libraries, pass-through for API. SW versioned via ETag for auto-updates.
- **Auto CheckIn Watcher (`app/core/watcher.py`)**: Polls every 60s for users with auto-checkin enabled. Scans unfinished GPS/数字 attendances within configured time windows and executes via `SessionPool`.
- **Auto CheckIn API (`app/routers/checkin.py`)**: Config, status, and manual trigger endpoints. Pydantic strict validation on time windows (0-23, start < end) and checkin types.
- **Testing (360+ tests)**: Tests organized by module under `tests/`. Route tests use FastAPI TestClient + SQLite. SchemaSync has 119 tests (diff, DDL, backup, migration, historical phases). Benchmark: median check-in latency < 50ms.

## Data Model

```
User ─── UserAccount ─── Account ─── CourseBinding ─── Course
                          Account ─── CheckInLog
                          Account ─── CourseLocation ─── Course
InviteCode
SystemSetting
```

- `User`: App users with admin/user roles
- `Account`: KetangPai credentials (password encrypted via Fernet), plus `username`, `school`, `stno`, `avatar`, `mobile`, `ktp_account`, `status_message`
- `UserAccount`: Many-to-many link table (users ↔ accounts). 含 `UniqueConstraint("user_id", "account_id")` 防止重复关联。
- `Course`: KetangPai courses (keyed by string ID from the API)
- `CourseBinding`: Links accounts to courses with `is_active` toggle
- `CheckInLog`: Per-account-per-course check-in records, with `message` field for result description
- `AutoCheckinConfig`: Per-user auto check-in configuration — `enabled`, `checkin_types`, `time_windows` (JSON array of `{start,end}` hour ranges)
- `InviteCode`: Registration invite codes with usage limits and expiry
- `SystemSetting`: Key-value system settings
- **课程同步**: `POST /api/accounts/{account_id}/sync-courses` 通过 SessionPool 拉取课堂派课程列表，增量创建 Course + CourseBinding。

## Environment

- Python >= 3.13, MySQL 8.0, Redis 8
- Dependencies managed by `uv` (see `pyproject.toml`)
- Copy `.env.example` -> `.env`, set `JWT_SECRET` (required)
- Generate `CREDENTIAL_KEY` with the Fernet command above (REQUIRED — no plaintext fallback)
- `DATABASE_URL` must be set (no default — startup will fail if missing)
- All config is managed via `app/core/settings.py` (pydantic-settings), not via `os.getenv`
- JWT 过期时间 ``JWT_EXPIRE_HOURS``（默认 24）和 ``JWT_REFRESH_EXPIRE_DAYS``（默认 30）可在 ``.env`` 中覆盖
- 签到日志保留天数 ``LOG_RETENTION_DAYS``（默认 90）和每账号最大条数 ``LOG_MAX_PER_ACCOUNT``（默认 500）可在 ``.env`` 中覆盖
