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
│   ├── login.html          # 独立登录/注册页面
│   ├── core/
│   │   ├── api.py          # KetangPai third-party API client (httpx async)
│   │   ├── settings.py     # Pydantic Settings — centralized config (reads .env)
│   │   ├── security.py     # Argon2 password hashing, JWT create/decode, Fernet encryption
│   │   ├── sessions.py     # SessionPool singleton — manages KetangPai login sessions
│   │   ├── watcher.py      # AutoCheckinWatcher — 后台自动签到观察器（轮询 + 执行）
│   │   ├── schema_sync.py  # SchemaSync — 全自动 schema 同步引擎（diff/backup/DDL/审计）
│   │   └── db.py           # SQLModel engine, Redis connection pool (breaker pattern), init_db (→ SchemaSync)
│   ├── routers/            # ★ Domain route modules (split from monolithic main.py)
│   │   ├── auth.py         # register, login, logout, refresh
│   │   ├── user.py         # user CRUD + change-password
│   │   ├── account.py      # account CRUD + verify + cascade delete
│   │   ├── course.py       # course CRUD + course-binding CRUD
│   │   ├── checkin.py      # batch check-in execution
│   │   ├── invite_code.py  # invite code CRUD
│   │   ├── log.py          # check-in log list/detail/delete
│   │   └── settings.py     # system settings (invite-required toggle)
│   ├── index.html          # Vue 3 SPA template（已登录）
│   └── login.html          # 独立登录/注册页面
├── tests/                  # ✅ 单元测试 + 集成测试 + 基准测试
│   ├── conftest.py         # 共享 fixtures + benchmark 结果收集器
│   ├── test_security.py    # 密码哈希、JWT、Fernet 加密、令牌黑名单
│   ├── test_models.py      # Pydantic/SQLModel 模型、_extract_gps、is_position_error
│   ├── test_utils.py       # get_client_ip、RateLimiter、_in_time_windows 等
│   ├── test_db.py          # _RedisWrapper 断路器、check_redis_health
│   ├── test_schema_sync.py # SchemaSync — 数据类、diff 引擎、DDL 编译、备份/审计
│   └── routers/
│       ├── __init__.py
│       ├── test_auth.py              # 注册/登录/登出/令牌刷新
│       ├── test_benchmark_checkin.py # 签到链路延迟基准测试 (median<50ms)
│       └── ...                        # 更多路由集成测试
├── static/                 # Client-side assets (local, no CDN)
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
├── scripts/                # Utility scripts
│   └── backfill_accounts.py  # Backfill user details for legacy accounts
├── docker-compose.yml      # MySQL 8 + Redis 7 + App
└── pyproject.toml           # 依赖管理 + pytest 配置
```

## Key Design Decisions

- **Routes split by domain**: The API surface is organized into domain router modules under `app/routers/`. When adding a new endpoint, locate the appropriate router file (`auth.py`, `account.py`, `course.py`, etc.) and add it there. Avoid adding routes to `app/main.py`.
- **Centralized config via pydantic-settings**: All configuration (DB, Redis, JWT, CORS, etc.) is defined in `app/core/settings.py` as a `Settings(BaseSettings)` class, loaded from `.env`. Never use `os.getenv()` directly.
- **Fully async architecture**: All KetangPai API calls use `httpx.AsyncClient`. SessionPool methods (`create`, `ensure_client`, `get_account_info`, `get_course_list`, `remove`) are all async. No `asyncio.to_thread` wrappers needed.
- **SessionPool (module-level singleton)**: Manages KetangPai API sessions with 3-layer concurrency control — `asyncio.Lock` (clients dict), `asyncio.Lock` (batch serialization), `asyncio.Semaphore(5)` (per-batch concurrency). Sessions expire after 30 min idle; tokens cached in Redis for 5 days.
- **Canary check-in (QR + GPS)**: Both QR and GPS check-in use canary mode — first account tested first. If it fails with code 30319/30322 (expired/ended), remaining accounts skip immediately and the failure is cached in Redis.
- **Redis check-in dedup**: QR: `checkin_done:{ticketid}:{account_id}` with TTL from ticket expiry. GPS: `checkin_done:gps:{attendance_id}:{account_id}` with TTL 24h. Prevents duplicate API calls.
- **JWT with httponly cookies**: Access tokens (24h) and refresh tokens (30d) are stored in httponly, SameSite=Lax cookies. The backend (`deps.py`) reads tokens from either `Authorization` header or `access_token` cookie. Frontend no longer manages tokens in localStorage.
- **Refresh Token Rotation**: Each refresh invalidates the old refresh token to prevent replay. Frontend automatically retries on 401 via cookie-based refresh.
- **Rate limiting**: Redis sliding window via `RateLimiter` dependency class — login/register 5 req/min, check-in 10 req/min.
- **Credential encryption**: Fernet (AES-128-CBC + HMAC) via `CREDENTIAL_KEY` env var. **Required at startup** — app will crash if unset.
- **Login business-level check**: `login()` inspects `result.status != 1` and raises with the API error message (e.g., "password expired"), rather than only checking HTTP status.
- **Account verification**: `POST /api/accounts/{id}/verify` re-logs in to KetangPai, updates status/status_message, and refreshes stored user details. Updating password also resets status automatically.
- **SchemaSync (`app/core/schema_sync.py`)**: 自动数据库结构同步引擎，替代旧 `_migrate()`。启动时自动完成：从 SQLModel 模型提取目标 schema → 通过 SQLAlchemy 反射获取当前状态 → `compute_diff()` 计算差异（含列改名启发式检测，Levenshtein + type-family 守卫）→ 纯 Python 备份受影响表 → DDL 执行（列/索引/外键，反引号保护保留字）→ 审计日志。快速路径：模型 SHA-256 哈希未变则跳过。受控于 `DB_AUTO_MIGRATE` 与 `DB_BACKUP_DIR` 设置。
- **Redis circuit breaker**: `_RedisWrapper` proxy auto-fuses on any operation failure, avoiding repeated timeouts. Health check pings Redis every 5 minutes.
- **Client IP detection**: `get_client_ip()` in `utils.py` reads `X-Forwarded-For` / `X-Real-IP` headers for reverse proxy setups before falling back to `request.client.host`.
- **Client IP forwarding to KetangPai**: The `/api/checkin` endpoint extracts the client's real IP via `get_client_ip(request)` and passes it through `SessionPool.execute_checkin()` → `KetangPaiAPI.check_in()`, which adds an `X-Forward-For` header to the outbound request to Ketangpai. Defaults to empty (no header sent) when IP is unavailable.
- **Frontend**: Two-page architecture — standalone `login.html` (no auth required) and `index.html` (main SPA, requires auth). Backend redirects `/` to `/login` if `access_token` cookie is missing. CSS split into `common.css` (shared), `login.css` (login page), `index.css` (main app). Separate `login.js` for auth logic.
- **Backend auth redirect**: The `/` route checks for `access_token` cookie. If absent, returns `RedirectResponse("/login")`. The `/login` page has no such protection.
- **Backend-forced auth check**: A new `GET /api/users/me` endpoint returns the current user's info, used by the frontend to recover from stale localStorage.
- **Async safety**: All KetangPai API methods are natively async (`httpx.AsyncClient`). No `asyncio.to_thread` wrappers needed — direct `await` on all API calls.
- **Auto CheckIn Watcher (`app/core/watcher.py`)**: Global `AutoCheckinWatcher` singleton polls every 60s for all users with auto-checkin enabled. Checks user's configured time windows (local hours), queries unfinished GPS/数字 attendances, and auto-executes check-in via `SessionPool`. All calls are async (no `asyncio.to_thread`). Falls back across multiple accounts if one fails. Manual trigger via `POST /api/auto-checkin/trigger`.
- **Auto CheckIn API (`app/routers/checkin.py`)**: Four endpoints — `GET/PUT /api/auto-checkin/config` (per-user config with strict Pydantic validation via `TimeWindow`/`AutoCheckinConfigBody`), `GET /api/auto-checkin/status` (watcher status + per-user `user_active` flag), `POST /api/auto-checkin/trigger` (manual scan trigger).
- **Pydantic strict validation on config**: `TimeWindow` model validates start/end hours (0-23, start < end), `AutoCheckinConfigBody` validates `checkin_types` (only "1"/"2"), `time_windows` (max 16 items, dedup). All manual JSON parsing/handling eliminated in favor of Pydantic validators.
- **Status uses `user_active` instead of `is_running`**: The global watcher is always running. Frontend shows meaningful status per user based on `user_active` (enabled + has time windows), not `is_running`.
- **Testing (289+ tests)**: Tests organized by module under `tests/`. Pure logic tested standalone; route tests use FastAPI TestClient with SQLite temp file (cross-thread safe) + mocked Redis (`None`). SchemaSync tests (`test_schema_sync.py`) cover data classes, schema extraction, diff engine, DDL compilation, backup, and integration flow (55 tests). Benchmark tests (`test_benchmark_checkin.py`) measure from endpoint to mock API, use 10 warmup + 10 measure + drop 1 outlier, assert median < 50ms. Results auto-saved to `tests/routers/.benchmark_results.json`.
- **`_in_time_windows` midnight-crossing support**: Time windows like `{start:22, end:6}` now correctly match hours 22–23 and 0–5, not just same-day ranges. Zero-width windows (`start == end`) never match.

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
- `AutoCheckinConfig`: Per-user auto check-in configuration — `enabled`, `checkin_types`, `time_windows` (JSON array of `{start,end}` hour ranges)
- `InviteCode`: Registration invite codes with usage limits and expiry
- `SystemSetting`: Key-value system settings

## Environment

- Python >= 3.13, MySQL 8.0, Redis 7
- Dependencies managed by `uv` (see `pyproject.toml`)
- Copy `.env.example` -> `.env`, set `JWT_SECRET` (required)
- Generate `CREDENTIAL_KEY` with the Fernet command above (REQUIRED — no plaintext fallback)
- `DATABASE_URL` must be set (no default — startup will fail if missing)
- All config is managed via `app/core/settings.py` (pydantic-settings), not via `os.getenv`
