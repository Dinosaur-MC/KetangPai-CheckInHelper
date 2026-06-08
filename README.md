# CheckInHelper — 课堂派签到助手

自动管理课堂派（ketangpai.com）账号并进行批量签到的 Web 应用。支持多用户、多账号、多课程绑定，提供管理后台和移动端友好的前端界面。

## 功能

- **用户系统** — 注册 / 登录 / 登出，JWT 认证，管理员角色
- **账号管理** — 添加课堂派账号（支持手机号/邮箱），自动校验有效性
- **课程绑定** — 添加账号后自动拉取学期课程并绑定，支持一键启用/禁用
- **批量签到** — 粘贴签到 URL 自动解析参数，并发处理多账号签到
- **签到日志** — 课程维度筛选，单次签到成功/失败明细
- **用户管理** — 管理员可创建/编辑/删除/禁用用户
- **会话池** — 30 分钟过期，token 缓存于 Redis，按需自动重建
- **安全机制** — 密码哈希（Argon2）、JWT 吊销、速率限制、CORS 白名单

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.13, FastAPI, SQLModel, uvicorn |
| 数据库 | MySQL + Redis |
| 前端 | Vue 3 (CDN), MDUI 2 (Web Components) |
| 安全 | Passlib (Argon2), PyJWT |
| 包管理 | uv |

## 项目结构

```
CheckInHelper/
├── main.py              # 入口：uvicorn 启动
├── pyproject.toml       # 依赖管理
├── .env                 # 环境变量（MySQL/Redis/JWT）
├── favicon.ico          # 网站图标
└── app/
    ├── main.py          # FastAPI 应用、路由、中间件、异常处理
    ├── models.py        # SQLModel 数据模型（User/Account/Course/CourseBinding/CheckInLog）
    ├── api.py           # 课堂派第三方 API 客户端（登录/课程/签到）
    ├── security.py      # 密码哈希、JWT 签发/验证、Token 黑名单
    ├── sessions.py      # 会话池（异步签到、并发限流、30min 过期）
    ├── db.py            # MySQL + Redis 连接池
    └── index.html       # 前端 SPA（Vue 3 + MDUI 2）
```

## 快速开始

### 环境要求

- Python >= 3.13
- MySQL 数据库
- Redis 服务

### 安装与配置

```bash
# 1. 克隆项目
git clone <repo-url> && cd CheckInHelper

# 2. 安装依赖
uv sync

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env：
#   DATABASE_URL=mysql+pymysql://user:pass@localhost:3306/checkinhelper?charset=utf8mb4
#   REDIS_URL=redis://localhost:6379/0
#   JWT_SECRET=<your-random-secret>
#   ALLOWED_ORIGINS=http://localhost:8765   # CORS 白名单
#   DEBUG=False                             # 控制日志级别

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
| `REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` |
| `JWT_SECRET` | JWT 签名密钥 | 未设置时随机生成（重启后全部 Token 失效） |
| `JWT_ALGORITHM` | JWT 算法 | `HS256` |
| `JWT_EXPIRE_HOURS` | Token 有效期 | `168`（7 天） |
| `ALLOWED_ORIGINS` | CORS 白名单（逗号分隔） | 空（不允许跨域） |
| `DEBUG` | 调试模式 | `false` |
| `DB_ECHO` | 打印 SQL 日志 | `false` |
| `DB_POOL_SIZE` | 数据库连接池大小 | `10` |

## API 端点

### 认证
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/register` | 用户注册 |
| POST | `/api/login` | 用户登录 |
| POST | `/api/logout` | Token 吊销 |

### 账号管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts` | 当前用户的课堂派账号列表 |
| POST | `/api/accounts` | 添加课堂派账号（自动验证并拉取课程） |
| PUT | `/api/accounts/{id}` | 更新账号信息 |
| DELETE | `/api/accounts/{id}` | 删除账号 |

### 课程管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/courses` | 课程列表（用户关联或全部） |
| GET | `/api/courses/{id}` | 课程详情 |
| DELETE | `/api/courses/{id}` | 删除课程（管理员） |
| GET | `/api/courses/bindings` | 当前用户的课程绑定 |
| POST | `/api/courses/bindings` | 创建课程绑定 |
| PUT | `/api/courses/bindings/{id}` | 切换绑定启用状态 |
| DELETE | `/api/courses/bindings/{id}` | 解绑（引用归零时自动删课程） |

### 签到
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/checkin` | 批量签到 |
| GET | `/api/checkin/logs` | 签到日志列表 |
| GET | `/api/checkin/logs/{id}` | 签到日志详情 |
| DELETE | `/api/checkin/logs/{id}` | 删除签到日志（管理员） |

### 用户管理（管理员）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users` | 用户列表 |
| GET | `/api/users/{id}` | 用户详情 |
| POST | `/api/users` | 创建用户 |
| PUT | `/api/users/{id}` | 更新用户 |
| DELETE | `/api/users/{id}` | 删除用户 |

## 登录流程

用户登录后获得 JWT Token，前端存储于 localStorage，每次请求通过 `Authorization: Bearer <token>` 发送。后端通过 token 中的 `jti` 支持黑名单吊销。

## 安全特性

- **CORS**：`ALLOWED_ORIGINS` 白名单机制，`allow_credentials=True`
- **速率限制**：登录/注册 5 次/分钟，签到 10 次/分钟（Redis 滑动窗口）
- **密码哈希**：用户密码使用 Argon2 哈希存储
- **JWT 吊销**：登出时将 `jti` 加入 Redis 黑名单
- **Token 随机生成**：`JWT_SECRET` 未设置时自动生成 64 位十六进制随机密钥

## 许可

MIT
