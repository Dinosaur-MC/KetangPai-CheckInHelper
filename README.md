# CheckInHelper — 课堂派签到助手

自动管理课堂派（ketangpai.com）账号并进行批量签到的 Web 应用。支持多用户、多账号、多课程绑定、扫码签到、邀请码注册，提供管理后台和移动端友好的前端界面。

## 功能

- **用户系统** — 注册 / 登录 / 登出，JWT 认证 + Refresh Token 轮换，管理员角色，修改密码
- **邀请码注册** — 管理员生成和管理邀请码，可选强制注册验证；三态：有效/停用/失效
- **账号管理** — 添加课堂派账号（支持手机号/邮箱），自动校验有效性并加密存储密码；已存在账号自动关联
- **课程绑定** — 添加账号后自动拉取学期课程并绑定，支持一键启用/禁用
- **批量签到** — 粘贴签到 URL 自动解析参数，并发处理多账号签到（Canary 模式）
- **扫码签到** — 调用摄像头实时扫描二维码（ZXing WASM），自动校验并执行签到；HTTP 环境降级为拍照识别
- **签到日志** — 课程维度筛选，单次签到成功/失败明细
- **用户管理** — 管理员可创建/编辑/删除/禁用用户，查看全部课堂派账号
- **会话池** — 30 分钟过期，token 缓存于 Redis，按需自动重建
- **安全机制** — 密码哈希（Argon2）、JWT 吊销 + Refresh Token Rotation、速率限制、CORS 白名单、凭据加密（Fernet）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.13, FastAPI, SQLModel, uvicorn |
| 数据库 | MySQL + Redis 5.x (RESP2) |
| 前端 | Vue 3, MDUI 2 (Web Components)，静态资源本地化 |
| 安全 | Passlib (Argon2), PyJWT, Cryptography (Fernet) |
| 包管理 | uv |

## 项目结构

```
CheckInHelper/
├── main.py              # 入口：uvicorn 启动
├── pyproject.toml       # 依赖管理
├── .env                 # 环境变量（MySQL/Redis/JWT）
├── favicon.ico          # 网站图标
├── app/
│   ├── main.py          # FastAPI 应用、路由、中间件、异常处理
│   ├── models.py        # SQLModel 数据模型
│   ├── api.py           # 课堂派第三方 API 客户端
│   ├── security.py      # 密码哈希、JWT 签发/验证、凭据加密
│   ├── sessions.py      # 会话池（异步签到、并发限流）
│   ├── db.py            # MySQL + Redis 连接池
│   └── index.html       # 前端 SPA 模板（Vue 3 + MDUI 2）
└── static/
    ├── index.css        # 前端样式
    ├── index.js         # 前端逻辑
    ├── mdui.css / mdui.global.js    # MDUI 2（本地化）
    ├── vue.global.prod.js            # Vue 3（本地化）
    ├── material-icons.css / .ttf     # Material Icons
    └── zxing.min.js                  # ZXing WASM QR 码解码库
```

## 快速开始

### 环境要求

- Python >= 3.13
- MySQL 数据库
- Redis 服务（可选，降级运行）

### 安装与配置

```bash
# 1. 克隆项目
git clone <repo-url> && cd CheckInHelper

# 2. 安装依赖
uv sync

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 DATABASE_URL 和 JWT_SECRET

# 4. 启动数据库（MySQL + Redis 需提前运行）

# 5. 启动服务
uv run main.py
```

服务默认监听 `http://0.0.0.0:8765`，首次启动时 SQLModel 会自动建表。

### 环境变量说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PORT` | 服务端口 | `8765` |
| `DATABASE_URL` | MySQL 连接串 | `mysql+pymysql://checkinhelper:checkinhelper@localhost:3306/checkinhelper` |
| `REDIS_URL` | Redis 连接串（支持密码） | `redis://localhost:6379/0` |
| `JWT_SECRET` | JWT 签名密钥 | 未设置时随机生成（重启后全部 Token 失效） |
| `JWT_ALGORITHM` | JWT 算法 | `HS256` |
| `JWT_EXPIRE_HOURS` | Token 有效期 | `168`（7 天） |
| `CREDENTIAL_KEY` | 课堂派密码加密密钥（Fernet） | 未设置时明文存储 |
| `ALLOWED_ORIGINS` | CORS 白名单（逗号分隔） | 空（不允许跨域） |
| `DEBUG` | 调试模式 | `false` |
| `DB_ECHO` | 打印 SQL 日志 | `false` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 连接池参数 | `10` / `20` |

## API 端点

### 认证
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/register` | 用户注册（可选 invite_code 字段） |
| POST | `/api/login` | 用户登录（返回 access_token + refresh_token） |
| POST | `/api/refresh` | 刷新令牌（Rotation 防重用） |
| POST | `/api/logout` | Token 吊销 |
| PUT | `/api/user/password` | 修改当前用户密码 |

### 账号管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts` | 当前用户的课堂派账号列表 |
| POST | `/api/accounts` | 添加课堂派账号（已存在则自动关联） |
| GET | `/api/accounts/{id}` | 获取指定账号信息 |
| PUT | `/api/accounts/{id}` | 更新账号信息 |
| DELETE | `/api/accounts/{id}` | 删除账号 |

### 课程管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/courses` | 课程列表 |
| GET | `/api/courses/{id}` | 课程详情 |
| DELETE | `/api/courses/{id}` | 删除课程（管理员） |
| GET | `/api/courses/bindings` | 当前用户的课程绑定 |
| POST | `/api/courses/bindings` | 创建课程绑定 |
| PUT | `/api/courses/bindings/{id}` | 切换绑定启用状态 |
| DELETE | `/api/courses/bindings/{id}` | 解绑 |

### 签到
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/checkin` | 批量签到（Canary 模式） |
| GET | `/api/checkin/logs` | 签到日志列表 |
| GET | `/api/checkin/logs/{id}` | 签到日志详情 |
| DELETE | `/api/checkin/logs/{id}` | 删除签到日志（管理员） |

### 用户管理（管理员）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users` | 用户列表 |
| GET | `/api/users/{id}` | 用户详情（也可查自己） |
| POST | `/api/users` | 创建用户 |
| PUT | `/api/users/{id}` | 更新用户 |
| DELETE | `/api/users/{id}` | 删除用户 |
| GET | `/api/admin/accounts` | 全部课堂派账号 |

### 邀请码（管理员）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/invite-codes` | 邀请码列表 |
| POST | `/api/invite-codes` | 生成邀请码（留空自动生成） |
| PUT | `/api/invite-codes/{id}` | 编辑（启用/停用/备注） |
| DELETE | `/api/invite-codes/{id}` | 删除邀请码 |
| GET | `/api/settings/invite-required` | 获取邀请码强制状态 |
| PUT | `/api/settings/invite-required` | 切换强制邀请码 |

## 关键设计

### 认证流程

登录/注册返回 `access_token`（短期）和 `refresh_token`（30 天）。前端拦截 401 自动用 `refresh_token` 换取新令牌（Rotation 策略，旧 token 一次使用后废弃）。页面刷新时本地解码 JWT `exp` 字段，过期 token 直接清除，不发起无效请求。

### 前端路由

- `#/login` / `#/register` — 登录 / 注册（独立 hash 路由）
- `#/dashboard` / `#/accounts` / `#/courses` / `#/checkin` / `#/logs` — 已登录页面
- `#/users` — 用户管理（仅管理员）
- 未登录直接访问需登录页面自动跳回 `#/login`
- 页面级滚动适配移动端 Edge 浮动工具栏

### 扫码签到

支持两种模式：
- **实时扫码**：打开摄像头 → ZXing WASM 逐帧分析 → 校验域名和参数 → 自动填充并执行签到
- **拍照扫码**：实时扫码不可用时（HTTP 环境）降级为拍照识别
- 去重防抖，原生分辨率扫描

### 邀请码三态

| 状态 | 条件 | 可恢复 |
|------|------|--------|
| 有效 | `is_active=true`，未过期，未超限 | — |
| 停用 | `is_active=false`，未过期，未超限 | 可恢复为有效 |
| 失效 | 已过期或用尽次数 | 不可恢复 |

注册流程先验证邀请码再查用户，成功注册才计入使用次数。

## 安全特性

- **凭据加密** — 课堂派账号密码使用 Fernet (AES-128-CBC + HMAC) 加密存储
- **Refresh Token Rotation** — 每次刷新使旧 token 失效，防止泄露后重放
- **JWT 吊销** — 登出时将 `jti` 加入 Redis 黑名单
- **速率限制** — 登录/注册 5 次/分钟（Redis 滑动窗口）
- **密码哈希** — Argon2 哈希存储
- **密码强度** — 8-128 字符，含大小写字母和数字
- **CORS** — `ALLOWED_ORIGINS` 白名单机制

## 许可

MIT
