<div align="center">

# 🎯 CheckInHelper · 课堂派签到助手

> 自动化课堂派（ketangpai.com）批量签到 Web 应用  
> 多用户 · 多账号 · 多课程 · 扫码签到 · 全栈管理

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=fff)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688?logo=fastapi&logoColor=fff)](https://fastapi.tiangolo.com)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=fff)](https://vuejs.org)
[![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=fff)](https://mysql.com)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=fff)](https://redis.io)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=fff)](docker-compose.yml)

</div>

---

## 📋 目录

- [🎯 CheckInHelper · 课堂派签到助手](#-checkinhelper--课堂派签到助手)
    - [📋 目录](#-目录)
    - [概述](#概述)
    - [功能特性](#功能特性)
        - [👤 用户系统](#-用户系统)
        - [🔑 课堂派账号管理](#-课堂派账号管理)
        - [📚 课程绑定](#-课程绑定)
        - [🚀 批量签到](#-批量签到)
        - [📷 扫码签到](#-扫码签到)
        - [📊 签到日志](#-签到日志)
        - [🔐 安全机制](#-安全机制)
        - [🛠️ 管理后台（仅管理员）](#️-管理后台仅管理员)
    - [系统架构](#系统架构)
        - [分层说明](#分层说明)
    - [技术栈](#技术栈)
    - [快速开始](#快速开始)
        - [Docker 部署（推荐）](#docker-部署推荐)
        - [手动部署](#手动部署)
    - [环境变量](#环境变量)
    - [前端界面](#前端界面)
        - [页面路由](#页面路由)
        - [UI 特点](#ui-特点)
    - [API 端点](#api-端点)
        - [认证](#认证)
        - [账号管理](#账号管理)
        - [课程管理](#课程管理)
        - [签到](#签到)
        - [用户管理（管理员）](#用户管理管理员)
        - [邀请码（管理员）](#邀请码管理员)
    - [关键设计](#关键设计)
        - [认证流程](#认证流程)
        - [签到引擎](#签到引擎)
        - [会话池管理](#会话池管理)
        - [扫码签到](#扫码签到)
    - [安全特性](#安全特性)
    - [项目结构](#项目结构)
    - [许可](#许可)

---

## 概述

**CheckInHelper** 是一个面向[课堂派](https://ketangpai.com)（在线教学平台）的签到自动化工具。它提供了一套完整的 **Web 管理界面 + RESTful API**，允许用户：

1. 添加多个课堂派账号（支持手机号/邮箱）
2. 将账号绑定到对应的课程
3. **一次操作**即可为绑定该课程的所有账号同时执行签到
4. 支持扫码识别签到二维码，实现全流程自动化

每个用户可管理独立的课堂派账号池，管理员可控制全局注册、管理用户和邀请码。

## 功能特性

### 👤 用户系统

| 功能        | 说明                                             |
| ----------- | ------------------------------------------------ |
| 注册 / 登录 | 支持邀请码注册（可选强制）和邮箱密码登录         |
| JWT 认证    | Access Token + Refresh Token Rotation 双令牌机制 |
| 角色管理    | `admin` 和 `user` 两级角色，权限分离             |
| 密码管理    | 修改密码、密码强度校验（含大小写字母+数字）      |

### 🔑 课堂派账号管理

- 添加账号时自动通过课堂派 API **验证凭据有效性**
- 密码使用 **Fernet (AES-128-CBC + HMAC)** 加密存储
- 已存在账号自动关联，多个用户可共享同一课堂派账号
- 添加成功后自动拉取学期课程列表并创建绑定

### 📚 课程绑定

- 灵活的多对多绑定关系：一个账号可绑定多个课程，一个课程可分配给多个账号
- 按课程启用/禁用签到开关，精细化控制
- 解绑时自动清理无引用的课程记录

### 🚀 批量签到

- **Canary 模式**：先用一个账号测试签到有效性，成功后再并发处理其余账号
- 签到结果区分成功、重复签到（视同成功）、二维码过期、考勤已结束
- 全局失败缓存：二维码过期/考勤结束后跳过所有账号，避免无效请求
- 签到结果实时显示，支持失败原因查看

### 📷 扫码签到

- 调用摄像头实时扫描二维码（**ZXing WASM** 解码引擎）
- 自动校验签到链接域名和参数完整性
- 识别成功后自动填充参数并执行签到
- **HTTP 环境降级**：摄像头不可用时支持拍照识别
- 原生分辨率扫描 + 图像预处理（灰度加权、对比度拉伸）

### 📊 签到日志

- 课程维度筛选、按时间倒序排列
- 首页统计面板：账号数、绑定数、今日签到、累计记录
- 签到明细查看（失败原因可追溯）

### 🔐 安全机制

- Argon2 密码哈希
- JWT 吊销 + Refresh Token Rotation（防止重放攻击）
- 速率限制（Redis 滑动窗口）
- CORS 白名单
- Fernet 凭据加密

### 🛠️ 管理后台（仅管理员）

- 用户管理（创建/编辑/禁用/删除）
- 全部课堂派账号查询
- 邀请码生成与管理
    - **三态模型**：有效 / 停用 / 失效
    - 支持使用次数上限和过期时间
    - 可切换强制/可选注册模式

## 系统架构

```mermaid
graph TB
    subgraph 用户浏览器["🌐 用户浏览器 (Vue 3 SPA)"]
        direction TB
        A1[登录 / 注册]
        A2[账号管理]
        A3[课程绑定]
        A4[签到执行]
        A5[日志查看]
    end

    subgraph 后端["⚡ FastAPI 后端 (uvicorn)"]
        direction TB
        B1[认证路由<br/>/api/auth]
        B2[账号路由<br/>/api/accounts]
        B3[签到路由<br/>/api/checkin]
        B4[管理路由<br/>/api/admin]
        B5[业务层<br/>SessionPool · KetangPaiAPI<br/>并发控制 · Canary · 缓存]
    end

    subgraph 数据层["💾 数据层"]
        C1[("MySQL<br/>SQLModel 持久化")]
        C2[("Redis<br/>缓存 · 限流 · TTL")]
    end

    D[("🌍 课堂派 API<br/>ketangpai.com/v5")]

    用户浏览器 -->|HTTP / JSON| 后端
    B1 --> B5
    B2 --> B5
    B3 --> B5
    B4 --> B5
    B5 --> C1
    B5 --> C2
    B5 --> D
```

### 分层说明

| 层级         | 职责                                                    |
| ------------ | ------------------------------------------------------- |
| **前端 SPA** | Vue 3 响应式 UI，MDUI 2 Material Design 组件，Hash 路由 |
| **API 路由** | FastAPI 路由，依赖注入（DB Session / Redis / 当前用户） |
| **业务逻辑** | 会话池管理、第三方 API 封装、Canary 签到策略            |
| **数据层**   | SQLModel ORM（MySQL）+ Redis 缓存 + 速率限制            |
| **外部依赖** | 课堂派 OpenAPI v5 + 签到页面接口                        |

## 技术栈

| 层级       | 技术                                         | 用途                                                  |
| ---------- | -------------------------------------------- | ----------------------------------------------------- |
| **后端**   | **Python 3.13+** → **FastAPI** → **uvicorn** | 高性能异步 Web 框架                                   |
| ORM        | **SQLModel** + **PyMySQL**                   | 类型安全的异步 ORM                                    |
| 数据库     | **MySQL 8.0**                                | 持久化存储                                            |
| 缓存       | **Redis 7**                                  | JWT 黑名单、会话 Token 缓存、速率限制、邀请码设置缓存 |
| **前端**   | **Vue 3** (Composition API)                  | 响应式 SPA                                            |
| UI 框架    | **MDUI 2** (Web Components)                  | Material Design 界面                                  |
| 图标       | **Material Icons**                           | 图标系统                                              |
| 扫码       | **ZXing WASM**                               | QR 码解码引擎                                         |
| **安全**   | **Passlib (Argon2)**                         | 密码哈希                                              |
|            | **PyJWT**                                    | JWT 签发与验证                                        |
|            | **Cryptography (Fernet)**                    | 凭据加密                                              |
|            | **Rate Limiter**                             | Redis 滑动窗口限流                                    |
| **包管理** | **uv**                                       | Python 依赖管理                                       |
| **部署**   | **Docker** + **docker compose**              | 容器化一站式部署                                      |

## 快速开始

### Docker 部署（推荐）

确保已安装 Docker 和 docker compose，然后在项目目录执行：

```bash
# 1. 克隆项目
git clone https://github.com/Dinosaur-MC/KetangPai-CheckInHelper && cd CheckInHelper

# 2. 创建环境变量（至少设置 JWT_SECRET）
cp .env.example .env
# 编辑 .env，设置 JWT_SECRET 等配置

# 3. 一键启动全部服务（MySQL + Redis + App）
docker compose up -d

# 4. 查看日志
docker compose logs -f app
```

> **首次启动会自动创建数据库表**，MySQL 和 Redis 通过健康检查确保就绪后应用才会启动。

```bash
# 停止
docker compose down

# 停止并删除数据卷
docker compose down -v
```

### 手动部署

**环境要求：** Python ≥ 3.13，MySQL 8.0，Redis 7

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 DATABASE_URL 和 JWT_SECRET

# 3. 确保 MySQL 和 Redis 已运行

# 4. 启动服务
uv run python main.py
```

服务默认监听 `http://0.0.0.0:8765`。

## 环境变量

项目通过 `.env` 文件配置，完整说明：

| 变量               | 说明                     | 默认值                                                                     |
| ------------------ | ------------------------ | -------------------------------------------------------------------------- |
| `PORT`             | 服务端口                 | `8765`                                                                     |
| `DATABASE_URL`     | MySQL 连接串             | `mysql+pymysql://checkinhelper:checkinhelper@localhost:3306/checkinhelper` |
| `REDIS_URL`        | Redis 连接串             | `redis://localhost:6379/0`                                                 |
| `JWT_SECRET`       | **JWT 签名密钥（必填）** | 未设置时随机生成（重启后全部 Token 失效）                                  |
| `JWT_ALGORITHM`    | JWT 算法                 | `HS256`                                                                    |
| `JWT_EXPIRE_HOURS` | Token 有效期（小时）     | `168`（7 天）                                                              |
| `CREDENTIAL_KEY`   | 课堂派密码加密密钥       | 未设置时明文存储                                                           |
| `ALLOWED_ORIGINS`  | CORS 白名单（逗号分隔）  | 空（不允许跨域）                                                           |
| `DEBUG`            | 调试模式                 | `false`                                                                    |
| `DB_ECHO`          | 打印 SQL 日志            | `false`                                                                    |
| `DB_POOL_SIZE`     | 数据库连接池大小         | `10`                                                                       |
| `DB_MAX_OVERFLOW`  | 连接池最大溢出           | `20`                                                                       |
| `DB_POOL_RECYCLE`  | 连接回收时间（秒）       | `3600`                                                                     |

> **生成 `CREDENTIAL_KEY`：**
>
> ```bash
> uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

## 前端界面

### 页面路由

| 路由          | 页面     | 说明                  |
| ------------- | -------- | --------------------- |
| `#/login`     | 登录     | 邮箱密码登录          |
| `#/register`  | 注册     | 支持邀请码注册        |
| `#/dashboard` | 首页概览 | 统计卡片 + 最近签到   |
| `#/accounts`  | 账号管理 | 管理课堂派账号        |
| `#/courses`   | 课程绑定 | 绑定课程到账号        |
| `#/checkin`   | 签到执行 | URL/手动/扫码三种方式 |
| `#/logs`      | 签到日志 | 历史记录查看与筛选    |
| `#/users`     | 用户管理 | 管理员专用            |

### UI 特点

- **响应式设计**：桌面端侧边栏布局，移动端底部 Sheet 导航
- **安全区域适配**：支持 iOS 刘海屏和移动端 Edge 底部工具栏
- **FAB 快捷操作**：首页浮动按钮直达签到页
- **Toast 通知**：操作结果实时反馈

## API 端点

完整的 OpenAPI 文档可在启动后访问：

- Swagger UI：[`http://localhost:8765/docs`](http://localhost:8765/docs)
- ReDoc：[`http://localhost:8765/redoc`](http://localhost:8765/redoc)

### 认证

| 方法 | 路径                 | 说明                                          | 权限   |
| ---- | -------------------- | --------------------------------------------- | ------ |
| POST | `/api/register`      | 用户注册（可选 `invite_code`）                | 公开   |
| POST | `/api/login`         | 登录（返回 `access_token` + `refresh_token`） | 公开   |
| POST | `/api/refresh`       | 刷新令牌（Rotation 防重用）                   | 公开   |
| POST | `/api/logout`        | Token 吊销                                    | 已登录 |
| PUT  | `/api/user/password` | 修改当前用户密码                              | 已登录 |

### 账号管理

| 方法   | 路径                 | 说明                               |
| ------ | -------------------- | ---------------------------------- |
| GET    | `/api/accounts`      | 当前用户的课堂派账号列表           |
| GET    | `/api/accounts/{id}` | 获取指定账号信息                   |
| POST   | `/api/accounts`      | 添加课堂派账号（已存在则自动关联） |
| PUT    | `/api/accounts/{id}` | 更新账号信息                       |
| DELETE | `/api/accounts/{id}` | 删除账号                           |

### 课程管理

| 方法   | 路径                         | 说明                       |
| ------ | ---------------------------- | -------------------------- |
| GET    | `/api/courses`               | 课程列表（管理员查看全部） |
| GET    | `/api/courses/{id}`          | 课程详情                   |
| DELETE | `/api/courses/{id}`          | 删除课程（管理员）         |
| GET    | `/api/courses/bindings`      | 当前用户的课程绑定         |
| POST   | `/api/courses/bindings`      | 创建课程绑定               |
| PUT    | `/api/courses/bindings/{id}` | 切换绑定启用状态           |
| DELETE | `/api/courses/bindings/{id}` | 解绑                       |

### 签到

| 方法   | 路径                     | 说明                                                |
| ------ | ------------------------ | --------------------------------------------------- |
| POST   | `/api/checkin`           | 批量签到（Canary 模式）                             |
| GET    | `/api/checkin/logs`      | 签到日志列表（支持 `account_id`、`course_id` 筛选） |
| GET    | `/api/checkin/logs/{id}` | 签到日志详情                                        |
| DELETE | `/api/checkin/logs/{id}` | 删除签到日志（管理员）                              |

### 用户管理（管理员）

| 方法   | 路径                  | 说明                         |
| ------ | --------------------- | ---------------------------- |
| GET    | `/api/users`          | 用户列表                     |
| GET    | `/api/users/{id}`     | 用户详情                     |
| POST   | `/api/users`          | 创建用户                     |
| PUT    | `/api/users/{id}`     | 更新用户（角色、状态、密码） |
| DELETE | `/api/users/{id}`     | 删除用户                     |
| GET    | `/api/admin/accounts` | 全部课堂派账号               |

### 邀请码（管理员）

| 方法   | 路径                            | 说明                               |
| ------ | ------------------------------- | ---------------------------------- |
| GET    | `/api/invite-codes`             | 邀请码列表                         |
| POST   | `/api/invite-codes`             | 生成邀请码（留空自动生成 16 位码） |
| PUT    | `/api/invite-codes/{id}`        | 编辑（启用/停用/备注/次数上限）    |
| DELETE | `/api/invite-codes/{id}`        | 删除邀请码                         |
| GET    | `/api/settings/invite-required` | 获取邀请码强制状态                 |
| PUT    | `/api/settings/invite-required` | 切换强制邀请码注册                 |

## 关键设计

### 认证流程

```
登录/注册
    │
    ▼
返回 access_token + refresh_token
    │
    ├── access_token ── 短期有效（默认 7天），用于请求认证
    │
    └── refresh_token ── 长期有效（默认 30天），用于令牌刷新
                            │
                            ▼
                        令牌刷新（Rotation 策略）
                            │
                            ├── 旧 refresh_token 标记为"已使用"
                            ├── 返回新的令牌对
                            └── 旧 access_token 登出时加入黑名单
```

**前端降级策略：**

- 本地解码 JWT `exp` 字段预判过期，直接清除不发起无效请求
- 拦截 401 后自动用 `refresh_token` 换新令牌并重试原请求
- 页面刷新保持登录状态

### 签到引擎

**Canary 模式 — 先测后发：**

```
收到签到请求（包含课程 ID + 签到参数）
    │
    ├── 查询该课程绑定的所有账号
    │
    ├── Canary 测试 ────────────────► 选第一个可用账号试签
    │       │                              │
    │       │                        ┌─────┴─────┐
    │       │                    成功 ▼           ▼ 失败
    │       │                  ┌──────────┐  缓存"签到无效"标记
    │       │                  │ 并发签到  │  (3600秒内跳过)
    │       │                  │ 所有账号  │
    │       │                  └──────────┘
    │       │                       │
    ▼       ▼                       ▼
记录签到日志（每个账号一条，含状态+消息）
```

**并发控制：**

- `asyncio.Semaphore(5)` 限制同一批次对课堂派 API 的并发数
- `asyncio.Lock` 序列化签到批次的执行阶段
- 失败缓存：`checkin:{courseid}:invalid:{ticketid}` 避免重复无效请求

### 会话池管理

```
SessionPool（模块级单例）
    │
    ├── clients: { account_id → (KetangPaiAPI, last_used) }
    │
    ├── TTL 30 分钟 ── 过期自动清理
    │
    ├── Token 缓存 Redis 5 天 ── 减少重复登录
    │
    ├── 按需重建 ── 无会话时从 DB 读取凭据并自动登录
    │
    └── 三层锁：
        ├── threading.Lock    保护 clients 字典（线程安全）
        ├── asyncio.Lock      序列化签到批次（协程安全）
        └── asyncio.Semaphore 控制并发请求数
```

### 扫码签到

```
扫码对话框打开
    │
    ├── 尝试打开后置摄像头（getUserMedia）
    │       │
    │       ├── 成功 → 实时视频流
    │       │          │
    │       │          ├── 逐帧截取 canvas（720p 上限）
    │       │          ├── 灰度加权 + 对比度拉伸预处理
    │       │          ├── ZXing WASM 解码
    │       │          └── 匹配课堂派域名 + 参数校验 → 自动签到
    │       │
    │       └── 失败 → 提示"使用拍照扫描"
    │
    └── 拍照扫描（降级方案）
              │
              ├── 调用系统相机（<input capture="environment">）
              ├── 加载照片 → canvas 绘制 → ZXing 解码
              └── 解析成功 → 自动签到
```

## 安全特性

| 措施                       | 实现                                                |
| -------------------------- | --------------------------------------------------- |
| **密码哈希**               | Argon2 via Passlib — 慢哈希抗暴力破解               |
| **JWT 签名**               | HS256/RS*/ES* 可选，密钥至少 16 字符                |
| **Refresh Token Rotation** | 每次刷新使旧 token 失效，防止泄露后重放             |
| **Token 吊销**             | 登出时将 `jti` 加入 Redis 黑名单，TTL 自动过期      |
| **速率限制**               | Redis 滑动窗口 — 登录/注册 5次/分钟，签到 10次/分钟 |
| **凭据加密**               | Fernet (AES-128-CBC + HMAC) 加密课堂派密码          |
| **密码强度**               | 8-128 字符，必须包含大小写字母和数字                |
| **CORS**                   | `ALLOWED_ORIGINS` 白名单机制，可配置多个来源        |
| **SQL 注入防护**           | SQLModel 参数化查询                                 |
| **异常处理**               | 全局异常处理器，敏感信息不暴露                      |

## 项目结构

```
CheckInHelper/
├── main.py                 # 🔵 入口 — uvicorn 启动
├── pyproject.toml          # 📦 依赖管理（uv）
├── .env.example            # 🔧 环境变量模板
├── Dockerfile              # 🐳 Docker 多阶段构建
├── docker-compose.yml      # 🐳 一键启动 (MySQL + Redis + App)
├── favicon.ico             # 🖼️ 网站图标
│
├── app/                    # 🧩 后端核心
│   ├── main.py             # FastAPI 应用、路由、中间件、异常处理
│   ├── api.py              # 课堂派第三方 API 客户端
│   ├── models.py           # SQLModel 数据模型定义
│   ├── security.py         # 密码哈希 · JWT 签发 · 凭据加密
│   ├── sessions.py         # 会话池（异步签到 · 并发限流）
│   ├── db.py               # MySQL + Redis 连接池
│   └── index.html          # 前端 SPA 模板
│
├── static/                 # 🎨 前端资源（本地化，无 CDN）
│   ├── index.css           # 应用样式
│   ├── index.js            # 应用逻辑（Vue 3 Composition API）
│   ├── mdui.css            # MDUI 2 组件样式
│   ├── mdui.global.js      # MDUI 2 脚本
│   ├── vue.global.prod.js  # Vue 3 运行时
│   ├── material-icons.css  # Material Icons 样式
│   ├── MaterialIcons-Regular.ttf  # 图标字体
│   └── zxing.min.js        # ZXing WASM QR 码解码
│
└── .claude/                # 🤖 Claude Code 配置
```

## 许可

**MIT License**

```
仅供学习研究使用。使用本项目时请遵守课堂派的相关服务条款。
```

---

<div align="center">
  <sub>Built with ❤️ for the open source community</sub>
</div>
