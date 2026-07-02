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
│   │   ├── log_cleanup.py  # LogCleanup — 签到日志过期清理和超限清理
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
├── static/                 # Client-side assets (local, no CDN)
│   ├── icons/              # PWA 图标 (180/192/512) + 截图 (mobile/desktop)
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
├── sw.js                   # Service Worker — PWA 离线/缓存策略
├── manifest.json           # PWA Web App Manifest
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
- **JWT with httponly cookies**: Access tokens (default 24h) and refresh tokens (default 30d) are stored in httponly, SameSite=Lax cookies. Backend (`deps.py`) reads tokens from either `Authorization` header or `access_token` cookie. Expirations configurable via `JWT_EXPIRE_HOURS` / `JWT_REFRESH_EXPIRE_DAYS` — cookie `max_age` stays in sync with JWT `exp`. Frontend no longer manages tokens in localStorage.
- **Refresh token cookie restricted to `/api/refresh`**: `refresh_token` cookie uses `path="/api/refresh"` so it's only sent to the refresh endpoint, not with every API request. `access_token` cookie uses `path="/"` for universal access.
- **Refresh Token Rotation**: Each refresh invalidates the old refresh token to prevent replay (`refresh_used:{jti}` in Redis). Frontend uses a Semaphore-based shared promise to ensure only one `POST /api/refresh` is made even with concurrent 401s.
- **Rate limiting**: Redis sliding window via `RateLimiter` dependency class — login/register 5 req/min, check-in 10 req/min.
- **Credential encryption**: Fernet (AES-128-CBC + HMAC) via `CREDENTIAL_KEY` env var. **Required at startup** — app will crash if unset.
- **Login business-level check**: `login()` inspects `result.status != 1` and raises with the API error message (e.g., "password expired"), rather than only checking HTTP status.
- **Account verification**: `POST /api/accounts/{id}/verify` re-logs in to KetangPai, updates status/status_message, and refreshes stored user details. Updating password also resets status automatically.
- **SchemaSync (`app/core/schema_sync.py`)**: 全自动数据库结构同步引擎，替代旧 `_migrate()`。启动时自动完成：从 SQLModel 模型提取目标 schema → 通过 SQLAlchemy 反射获取当前状态 → `compute_diff()` 计算差异（含列改名启发式检测，Levenshtein + type-family 守卫 + FK 列排除 + 默认距离 2；BOOLEAN↔TINYINT(1) 类型统一规范化防虚假 diff）→ 纯 Python 流式备份受影响表（`yield_per` 分批，防 OOM）→ DDL 执行（列/索引/外键，反引号保护保留字，AUTO_INCREMENT/PK/COMMENT 完整输出；`ALTER COLUMN ... SET/DROP DEFAULT` 对齐模型与数据库的默认值）→ 审计日志。快速路径：模型 SHA-256 哈希未变则跳过。**安全特性**：幂等执行（DDL 重复安全）、迁移锁（`_acquire_migration_lock` 原子 INSERT，防多实例并发）、`NOT NULL` 无默认值警告、备份自动清理（`DB_BACKUP_RETENTION_DAYS`）、备份 SQL 含 `SET NAMES utf8mb4`。受控于 `DB_AUTO_MIGRATE` / `DB_BACKUP_DIR` / `DB_BACKUP_RETENTION_DAYS` 设置。
- **LogCleanup (pp/core/log_cleanup.py)**: 签到日志自动清理模块。两个独立策略——**过期清理**（默认 90 天）和**超限清理**（每账号默认 500 条）。后台每日自动执行（_log_cleanup_loop 在 lifespan 中注册，启动后立即执行一次，之后每 24h 运行），同时管理员可手动触发 POST /api/logs/cleanup。使用 MySQL 8+ 窗口函数 ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY created_at DESC) 实现高效超限删除。受控于 LOG_RETENTION_DAYS / LOG_MAX_PER_ACCOUNT 设置。session.commit() 确保删除持久化。

- **Redis circuit breaker**: `_RedisWrapper` proxy auto-fuses on any operation failure, avoiding repeated timeouts. Health check pings Redis every 5 minutes.
- **Client IP detection**: `get_client_ip()` in `utils.py` reads `X-Forwarded-For` / `X-Real-IP` headers for reverse proxy setups before falling back to `request.client.host`.
- **Client IP forwarding to KetangPai**: The `/api/checkin` endpoint extracts the client's real IP via `get_client_ip(request)` and passes it through `SessionPool.execute_checkin()` → `KetangPaiAPI.check_in()`, which adds an `X-Forward-For` header to the outbound request to Ketangpai. Defaults to empty (no header sent) when IP is unavailable.
- **Frontend**: Two-page architecture — standalone `login.html` (login/register) and `index.html` (main SPA). CSS split into `common.css` (shared), `login.css` (login page), `index.css` (main app). Separate `login.js` for auth logic.
- **Root route serves SPA unconditionally**: `GET /` always returns `index.html`. Auth is handled entirely by the frontend — Vue app checks `/api/users/me`, if 401 it tries `POST /api/refresh` via the shared Semaphore. Only if refresh also fails does it redirect to `/login`.
- **Frontend 401 retry with Semaphore**: `index.js` defines a `Semaphore(1)` class and `_refreshToken()` function. On any `api()` 401, the first caller acquires the semaphore and calls `POST /api/refresh`. Subsequent concurrent callers see `_tokenRefreshed=true` and skip. After refresh, all retry with the new `access_token` cookie.
- **Async safety**: All KetangPai API methods are natively async (`httpx.AsyncClient`). No `asyncio.to_thread` wrappers needed — direct `await` on all API calls.
- **PWA (Progressive Web App)**: Fully installable — `manifest.json` at root with `display: standalone`, theme-color `#2563eb`, 192+512 maskable icons, and mobile/desktop screenshots. `sw.js` Service Worker at root implements three-tier caching:
  - **HTML + app JS/CSS** (`index.js`, `index.css`, `login.js`, `login.css`, `common.css`, `favicon.ico`): **network-first** — online always fetches latest from server; offline falls back to cache. Ensures code updates are visible immediately.
  - **Large libraries** (`opencv.js`, `wechat_qrcode`, `zxing.min.js`, `vue`, `mdui`, fonts, images): **cache-first** — installed once via `cache.addAll()` during SW install, never re-fetched.
  - **API calls** (`/api/*`): pass-through, never cached.
- **SW versioning via ETag**: `sw.js` served from FastAPI route with `ETag` (file mtime+size) and `Cache-Control: no-cache`. Browser sends `If-None-Match` for conditional 304 responses; SW updates detected automatically when the file changes.
- **Manual refresh button**: Top-right banner in the SPA has a refresh button (for PWA standalone mode where pull-to-refresh is unavailable). Uses DOM-based throttle (2s cooldown) with spinning animation.
- **PWA icons**: Generated from `favicon.ico` via ImageMagick — 180×180 (iOS), 192×192 (Android), 512×512 (install prompt + maskable). Screenshots (720×1280 mobile + 1280×720 desktop) for rich install UI.
- **Auto CheckIn Watcher (`app/core/watcher.py`)**: Global `AutoCheckinWatcher` singleton polls every 60s for all users with auto-checkin enabled. Checks user's configured time windows (local hours), queries unfinished GPS/数字 attendances, and auto-executes check-in via `SessionPool`. All calls are async (no `asyncio.to_thread`). Falls back across multiple accounts if one fails. Manual trigger via `POST /api/auto-checkin/trigger`.
- **Auto CheckIn API (`app/routers/checkin.py`)**: Four endpoints — `GET/PUT /api/auto-checkin/config` (per-user config with strict Pydantic validation via `TimeWindow`/`AutoCheckinConfigBody`), `GET /api/auto-checkin/status` (watcher status + per-user `user_active` flag), `POST /api/auto-checkin/trigger` (manual scan trigger).
- **Pydantic strict validation on config**: `TimeWindow` model validates start/end hours (0-23, start < end), `AutoCheckinConfigBody` validates `checkin_types` (only "1"/"2"), `time_windows` (max 16 items, dedup). All manual JSON parsing/handling eliminated in favor of Pydantic validators.
- **Status uses `user_active` instead of `is_running`**: The global watcher is always running. Frontend shows meaningful status per user based on `user_active` (enabled + has time windows), not `is_running`.
- **Testing (360+ tests)**: Tests organized by module under `tests/`. Pure logic tested standalone; route tests use FastAPI TestClient with SQLite temp file (cross-thread safe) + mocked Redis (`None`). SchemaSync tests (`test_schema_sync.py`, 119 tests) cover data classes, schema extraction, diff engine, DDL compilation, backup, integration flow, migration lock, backup cleanup, idempotency, COMMENT support, charset declaration, default alignment (`ALTER COLUMN ... SET/DROP DEFAULT`), BOOLEAN/TINYINT normalization, **historical phase migration paths** (6 phases), **Cartesian product permutation tests** (5 seeds + 24 full permutations), and **future predicted change tests** (5 scenarios). Benchmark tests (`test_benchmark_checkin.py`) measure from endpoint to mock API, use 10 warmup + 10 measure + drop 1 outlier, assert median < 50ms. Results auto-saved to `tests/routers/.benchmark_results.json`.
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
- JWT 过期时间 ``JWT_EXPIRE_HOURS``（默认 24）和 ``JWT_REFRESH_EXPIRE_DAYS``（默认 30）可在 ``.env`` 中覆盖
- 签到日志保留天数 ``LOG_RETENTION_DAYS``（默认 90）和每账号最大条数 ``LOG_MAX_PER_ACCOUNT``（默认 500）可在 ``.env`` 中覆盖
