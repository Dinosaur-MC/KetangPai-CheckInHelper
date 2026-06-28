<div align="center">

# 🎯 CheckInHelper · 课堂派签到助手

> 自动化课堂派（ketangpai.com）批量签到 Web 应用  
> 多用户 · 多账号 · 多课程 · 扫码签到 · 全栈管理

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=fff)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.138+-009688?logo=fastapi&logoColor=fff)](https://fastapi.tiangolo.com)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=fff)](https://vuejs.org)
[![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=fff)](https://mysql.com)
[![Redis](https://img.shields.io/badge/Redis-8-DC382D?logo=redis&logoColor=fff)](https://redis.io)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=fff)](docker-compose.yml)
[![Tests](https://github.com/Dinosaur-MC/KetangPai-CheckInHelper/actions/workflows/pytest.yml/badge.svg)](https://github.com/Dinosaur-MC/KetangPai-CheckInHelper/actions/workflows/pytest.yml)
[![Benchmark](https://img.shields.io/badge/Latency-%3C50ms-4FC08D)](tests/routers/.benchmark_results.json)

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
        - [⏰ 自动签到](#-自动签到)
        - [📊 签到日志](#-签到日志)
        - [🔐 安全机制](#-安全机制)
        - [🛠️ 管理后台（仅管理员）](#️-管理后台仅管理员)
    - [系统架构](#系统架构)
        - [分层说明](#分层说明)
    - [技术栈](#技术栈)
    - [快速开始](#快速开始)
        - [Docker 部署（推荐）](#docker-部署推荐)
        - [手动部署](#手动部署)
        - [运行测试](#运行测试)
        - [延迟基准测试](#延迟基准测试)
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
- 自动获取并存储用户详情：姓名、学校、学号、院系、手机号、头像
- 凭据验证按钮：随时手动重新验证账号状态，验证成功时同步刷新用户信息
- 更新密码时自动重置账号状态并重新登录验证

### 📚 课程绑定

- 灵活的多对多绑定关系：一个账号可绑定多个课程，一个课程可分配给多个账号
- 按课程启用/禁用签到开关，精细化控制
- 解绑时自动清理无引用的课程记录

### 🚀 批量签到

- **Canary 模式**：先用一个账号测试签到有效性，成功后再并发处理其余账号
- 签到结果区分成功、重复签到（视同成功）、二维码过期、考勤已结束
- 全局失败缓存：二维码过期/考勤结束后跳过所有账号，避免无效请求
- **Redis 签到去重**：同一 ticketid 下已签到成功的账号自动跳过，防止重复调用 API
- 签到结果实时显示，支持失败原因查看
- **客户端 IP 透传**：签到请求经过后端时自动提取客户端真实 IP，以 `X-Forward-For` 请求头传递至课堂派 API，避免所有签到请求显示为同一服务器 IP

### 📷 扫码签到

- 调用摄像头实时扫描二维码（**OpenCV WeChat QR** 主解码引擎 + **ZXing WASM** 备用）
- WeChat QR 引擎基于 C++ WASM，对倾斜 / 畸变 / 低光照二维码识别率更高
- 自动校验签到链接域名和参数完整性
- 识别成功后自动填充参数并执行签到
- **HTTP 环境降级**：摄像头不可用时支持拍照识别
- 原生分辨率扫描 + 图像预处理（灰度加权、对比度拉伸）
- 轮询超时机制，避免长时间无结果卡死
- WASM 内存泄漏和 Blob URL 泄漏自动清理

### ⏰ 自动签到

- **后台轮询**：系统每 60 秒自动检查所有开启了自动签到的用户，扫描绑定课程中未完成的 GPS/数字考勤并自动签到
- **签到类型选择**：支持数字考勤 (1) 和 GPS 考勤 (2)，可单独启用或组合使用
- **多时段配置**：可配置每日多个运行时段（如 8:00-12:00 和 14:00-22:00），仅在配置时段内执行
- **去重保护**：重复的时段自动去重，最多支持 16 个时段
- **运行状态面板**：实时显示自动签到是否生效、上次检查时间和结果
- **手动触发**：一键立即扫描，无需等待轮询周期
- **严格 Pydantic 校验**：时段 start/end 范围 0-23 且 start < end，checkin_types 仅允许 "1"/"2"

### 📊 签到日志

- 多维度筛选：按**账号（邮箱）**、**课程 ID**、**签到结果**（成功/失败）、**日期范围**
- 首页统计面板：账号数、绑定数、今日签到、累计记录
- 签到明细查看（失败原因可追溯），支持筛选条件变化 300ms 防抖自动重载
- 每条日志附带签到结果描述（成功 / 二维码过期 / 重复签到等）
- **自动清理**：每日后台自动清理超过 90 天的旧日志，并确保每账号最多保留 500 条记录（可配置）

### 🔐 安全机制

- Argon2 密码哈希
- JWT 吊销 + Refresh Token Rotation（防止重放攻击）
- 速率限制（Redis 滑动窗口）
- Redis 断路器模式（健康检查 + 自动恢复）
- CORS 白名单
- Fernet 凭据加密

### 🛠️ 管理后台（仅管理员）

- 用户管理（创建/编辑/禁用/删除）
- 全部课堂派账号查询
- 邀请码生成与管理
    - **三态模型**：有效 / 停用 / 失效
    - 支持使用次数上限和过期时间
    - 可切换强制/可选注册模式
- 签到日志管理（查看、删除、手动触发日志清理）

## 系统架构

<table align="center">
  <tbody>
    <tr><th colspan="2" align="center">🌐 前端 Vue 3 SPA</th></tr>
    <tr>
      <td><strong>页面路由</strong><br/>登录 · 注册 · 首页<br/>账号管理 · 课程绑定<br/>签到执行 · 签到日志 · 用户管理</td>
      <td><strong>扫码引擎</strong><br/>OpenCV WeChat QR<br/>ZXing WASM</td>
    </tr>
    <tr><th colspan="2" align="center">⚡ FastAPI 后端</th></tr>
    <tr><td colspan="2"><strong>🛡️ 中间件</strong><br/>JWT 认证 (Access + Refresh Rotation) · Redis 滑动窗口限流 · CORS · 全局异常处理</td></tr>
    <tr><td colspan="2"><strong>🧭 路由层 · 8 领域模块</strong><br/>auth / account / course / checkin / log / user / invite_code / settings</td></tr>
    <tr><td colspan="2"><strong>⚙️ 业务逻辑层</strong><br/>SessionPool 会话池 · KetangPaiAPI · AutoCheckinWatcher · LogCleanup 日志清理 · Canary 引擎 · Fernet/AES 加密 · Argon2 哈希 · 缓存策略</td></tr>
    <tr><td colspan="2"><strong>💾 数据层</strong></td></tr>
    <tr>
      <td><strong>MySQL 8</strong><br/>SQLModel ORM · SchemaSync 自动迁移</td>
      <td><strong>Redis 8</strong><br/>缓存 · 限流 · 断路器</td>
    </tr>
    <tr><th colspan="2" align="center"><strong>🌍 课堂派 API</strong><br/>ketangpai.com — 签到接口</th></tr>
  </tbody>
</table>

### 分层说明

| 层级             | 职责                                                                                                              |
| ---------------- | ----------------------------------------------------------------------------------------------------------------- |
| **前端 SPA**     | Vue 3 响应式 UI，MDUI 2 Material Design 组件，Hash 路由，扫码引擎 (WeChat QR + ZXing)                             |
| **安全与中间件** | JWT 认证（Access + Refresh Token Rotation）、Redis 滑动窗口限流、全局异常处理、CORS                               |
| **路由层**       | 8 个领域路由模块（auth/account/course/checkin/log/user/invite_code/settings），依赖注入                           |
| **业务逻辑层**   | SessionPool 会话池、KetangPaiAPI 封装、AutoCheckinWatcher 自动签到、LogCleanup 日志清理、Canary 签到引擎、Fernet/Argon2 加密、缓存策略 |
| **数据层**       | SQLModel ORM（MySQL 8）持久化 + Redis 8 缓存/限流/断路器                                                          |
| **外部依赖**     | 课堂派 OpenAPI + 签到页面接口                                                                                     |

## 技术栈

| 层级       | 技术                                         | 用途                                                  |
| ---------- | -------------------------------------------- | ----------------------------------------------------- |
| **后端**   | **Python 3.13+** → **FastAPI** → **uvicorn** | 高性能异步 Web 框架                                   |
| ORM        | **SQLModel** + **PyMySQL**                   | 类型安全的异步 ORM                                    |
| 数据库     | **MySQL 8.0**                                | 持久化存储                                            |
| 缓存       | **Redis 8**                                  | JWT 黑名单、会话 Token 缓存、速率限制、邀请码设置缓存 |
| API 客户端 | **httpx**                                    | 异步 KetangPai API 客户端（async, 非阻塞）            |
| **前端**   | **Vue 3** (Composition API)                  | 响应式 SPA（独立登录页 + 主应用）                     |
| UI 框架    | **MDUI 2** (Web Components)                  | Material Design 界面                                  |
| 样式       | **common.css + login.css + index.css**       | 分层 CSS：公共 / 登录页 / 主应用                      |
| 图标       | **Material Icons**                           | 图标系统                                              |
| 扫码(主)   | **OpenCV.js (WeChat QR)**                    | C++ WASM 解码引擎，抗畸变倾斜，识别率更高             |
| 扫码(备)   | **ZXing WASM**                               | WASM 备用 QR 解码引擎                                 |
| **安全**   | **Passlib (Argon2)**                         | 密码哈希                                              |
|            | **PyJWT**                                    | JWT 签发与验证（httponly cookie 承载）                |
|            | **Cryptography (Fernet)**                    | 凭据加密                                              |
|            | **Rate Limiter**                             | Redis 滑动窗口限流                                    |
| **测试**   | **pytest** + **httpx** (TestClient)           | 360+ 个单元 + 集成 + 基准测试 |
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

**环境要求：** Python ≥ 3.13，MySQL 8.0，Redis 8

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

### 运行测试

项目包含 **360 个测试**（119 个 SchemaSync 测试 + 单元测试 + 集成测试 + 延迟基准测试），覆盖安全模块、数据模型、工具函数、数据库层、SchemaSync（含历史阶段迁移路径、笛卡尔积排列测试、未来变更预测、默认值对齐、BOOLEAN/TINYINT 类型规范化）、账号模块、课程模块、自动签到模块、签到日志清理模块和认证路由：

```bash
# 运行全部测试
uv run pytest

# 查看详细输出
uv run pytest -v

# 仅运行单元测试
uv run pytest tests/test_*.py -v

# 运行指定文件
uv run pytest tests/test_security.py -v
uv run pytest tests/routers/ -v          # 路由集成测试
uv run pytest -v -s tests/routers/test_benchmark_checkin.py  # 基准测试详情
```

测试使用 **SQLite 临时文件** 替代 MySQL，**Redis 模拟为 None**，无需启动外部依赖即可运行。

### 延迟基准测试

签到链路的延迟基准测试随常规测试自动运行，结果保存在 `tests/routers/.benchmark_results.json`：

| 场景 | median | avg | p90 | 样本 |
|------|--------|-----|-----|------|
| 5 账号并发签到 | ~15 ms | ~15 ms | ~16 ms | 10轮测量去最慢1个 |
| 10 账号 | ~15 ms | ~16 ms | ~16 ms | 同上 |
| 20 账号 | ~16 ms | ~16 ms | ~17 ms | 同上 |
| 50 账号 | ~19 ms | ~19 ms | ~21 ms | 同上 |

防抖动策略：10 轮 warmup → 10 轮测量 → 去掉最慢的 1 个样本 → 断言 **median < 50ms**。

## 环境变量

项目通过 `.env` 文件配置，所有配置项由 `app/core/settings.py`（pydantic-settings）统一管理：

> **注意**：`DATABASE_URL` 和 `CREDENTIAL_KEY` 为 **必需项**，未设置时启动会报错退出。

| 变量               | 说明                           | 默认值                         |
| ------------------ | ------------------------------ | ------------------------------ |
| `DATABASE_URL`     | **MySQL 连接串（必填）**       | 无（未设置时启动失败）         |
| `REDIS_URL`        | Redis 连接串                   | `redis://localhost:6379/0`     |
| `JWT_SECRET`       | **JWT 签名密钥（必填）**       | 未设置时随机生成（重启后失效） |
| `JWT_ALGORITHM`    | JWT 算法                       | `HS256`                        |
| `JWT_EXPIRE_HOURS` | Access Token 有效期            | `24`（小时）                   |
| `CREDENTIAL_KEY`   | **课堂派密码加密密钥（必填）** | 无（未设置时启动失败）         |
| `ALLOWED_ORIGINS`  | CORS 白名单（逗号分隔）        | 空（不允许跨域）               |
| `PORT`             | 服务端口                       | `8765`                         |
| `DEBUG`            | 调试模式（热重载）             | `false`                        |
| `DB_ECHO`          | 打印 SQL 日志                  | `false`                        |
| `DB_POOL_SIZE`     | 数据库连接池大小               | `10`                           |
| `DB_MAX_OVERFLOW`  | 连接池最大溢出                 | `20`                           |
| `DB_POOL_RECYCLE`  | 连接回收时间（秒）             | `3600`                         |
| `DB_AUTO_MIGRATE`  | 启动时自动同步数据库结构（SchemaSync） | `true`                  |
| `DB_BACKUP_DIR`    | 数据库备份目录（SchemaSync DDL 前自动备份） | `./backups`          |
| `DB_BACKUP_RETENTION_DAYS` | 备份保留天数（30 天后自动清理旧备份，0 不清理） | `30`     |
| `LOG_RETENTION_DAYS`     | 签到日志保留天数（超过此天数在每日清理时删除）       | `90`      |
| `LOG_MAX_PER_ACCOUNT`     | 每账号最大日志条数（超出部分在每日清理时删除）       | `500`     |

> **生成 `CREDENTIAL_KEY`：**
>
> ```bash
> uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

## 前端界面

### 页面路由

| 路由          | 页面     | 说明                           |
| ------------- | -------- | ------------------------------ |
| `#/login`     | 登录     | 邮箱密码登录                   |
| `#/register`  | 注册     | 支持邀请码注册                 |
| `#/dashboard` | 首页概览 | 统计卡片 + 最近签到            |
| `#/accounts`  | 账号管理 | 管理课堂派账号                 |
| `#/courses`   | 课程绑定 | 绑定课程到账号                 |
| `#/checkin`   | 签到执行 | URL/手动/扫码/自动签到四种方式 |
| `#/logs`      | 签到日志 | 历史记录查看与筛选             |
| `#/users`     | 用户管理 | 管理员专用                     |

### UI 特点

- **响应式设计**：桌面端侧边栏布局，移动端底部 Sheet 导航
- **安全区域适配**：支持 iOS 刘海屏和移动端 Edge 底部工具栏
- **FAB 快捷操作**：首页浮动按钮直达签到页
- **Toast 通知**：操作结果实时反馈

## API 端点

完整的 OpenAPI 文档可在启动后访问：

- Swagger UI：[`http://localhost:8765/docs`](http://localhost:8765/docs)
- ReDoc：[`http://localhost:8765/redoc`](http://localhost:8765/redoc)

> **分页说明**：所有列表接口均支持 `page` 和 `page_size` 查询参数（默认 `page=1, page_size=20`），响应额外返回 `total`、`page`、`page_size` 字段。

### 认证

| 方法 | 路径                 | 说明                                          | 权限   |
| ---- | -------------------- | --------------------------------------------- | ------ |
| POST | `/api/register`      | 用户注册（可选 `invite_code`）                | 公开   |
| POST | `/api/login`         | 登录（返回 `access_token` + `refresh_token`） | 公开   |
| POST | `/api/refresh`       | 刷新令牌（Rotation 防重用）                   | 公开   |
| POST | `/api/logout`        | Token 吊销                                    | 已登录 |
| PUT  | `/api/user/password` | 修改当前用户密码                              | 已登录 |

### 账号管理

| 方法   | 路径                        | 说明                                             |
| ------ | --------------------------- | ------------------------------------------------ |
| GET    | `/api/accounts`             | 当前用户的课堂派账号列表（分页）                 |
| GET    | `/api/accounts/{id}`        | 获取指定账号信息                                 |
| POST   | `/api/accounts`             | 添加课堂派账号（已存在则自动关联）               |
| PUT    | `/api/accounts/{id}`        | 更新账号信息（更新密码会自动重置状态并刷新详情） |
| POST   | `/api/accounts/{id}/verify` | 重新验证账号凭据有效性，刷新用户详情             |
| DELETE | `/api/accounts/{id}`        | 删除账号                                         |

### 课程管理

| 方法   | 路径                         | 说明                             |
| ------ | ---------------------------- | -------------------------------- |
| GET    | `/api/courses`               | 课程列表，管理员查看全部（分页） |
| GET    | `/api/courses/{id}`          | 课程详情                         |
| DELETE | `/api/courses/{id}`          | 删除课程（管理员）               |
| GET    | `/api/courses/bindings`      | 当前用户的课程绑定（分页）       |
| POST   | `/api/courses/bindings`      | 创建课程绑定                     |
| PUT    | `/api/courses/bindings/{id}` | 切换绑定启用状态                 |
| DELETE | `/api/courses/bindings/{id}` | 解绑                             |

### 签到

| 方法   | 路径                        | 说明                                                                                      |
| ------ | --------------------------- | ----------------------------------------------------------------------------------------- |
| POST   | `/api/checkin`              | 批量签到（Canary 模式）                                                                   |
| POST   | `/api/checkin/gps`          | GPS 位置签到                                                                              |
| GET    | `/api/auto-checkin/config`  | 获取当前用户自动签到配置                                                                  |
| PUT    | `/api/auto-checkin/config`  | 更新自动签到配置（严格 Pydantic 校验）                                                    |
| GET    | `/api/auto-checkin/status`  | 自动签到运行状态 + 当前用户生效状态                                                       |
| POST   | `/api/auto-checkin/trigger` | 手动触发一次自动签到扫描                                                                  |
| GET    | `/api/logs/checkin`         | 签到日志列表，支持 `account_email`/`course_id`/`status`/`date_from`/`date_to` 筛选 + 分页 |
| GET    | `/api/logs/checkin/{id}`    | 签到日志详情                                                                              |
| DELETE | `/api/logs/checkin/{id}`    | 删除签到日志（管理员）                                                                    |
| POST   | `/api/logs/cleanup`          | 手动触发签到日志清理（过期删除 + 超限删除）（管理员）                                 |

### 用户管理（管理员）

| 方法   | 路径                  | 说明                         |
| ------ | --------------------- | ---------------------------- |
| GET    | `/api/users`          | 用户列表（分页）             |
| GET    | `/api/users/{id}`     | 用户详情                     |
| POST   | `/api/users`          | 创建用户                     |
| PUT    | `/api/users/{id}`     | 更新用户（角色、状态、密码） |
| DELETE | `/api/users/{id}`     | 删除用户                     |
| GET    | `/api/admin/accounts` | 全部课堂派账号（分页）       |

### 邀请码（管理员）

| 方法   | 路径                            | 说明                               |
| ------ | ------------------------------- | ---------------------------------- |
| GET    | `/api/invite-codes`             | 邀请码列表（分页）                 |
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
    ├── access_token ── 短期有效（默认 24小时），用于请求认证
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
- 成功去重：`checkin_done:{ticketid}:{account_id}` 防止同一 ticket 下重复调用签到 API

### 日志清理

**双策略自动清理：**

```
每日后台清理循环（启动后立即执行一次，之后每 24h）
    │
    ├── 过期清理 ──── DELETE FROM checkinlog WHERE created_at < NOW() - 90 天
    │                    （LOG_RETENTION_DAYS 可配置）
    │
    └── 超限清理 ──── 使用 ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY created_at DESC)
                        删除排名 > LOG_MAX_PER_ACCOUNT（默认 500）的旧记录
                        保留每个账号最新的 N 条日志
```

**两种触发方式：**

- **后台自动**：应用启动时注册的 _log_cleanup_loop 协程，启动后立即执行一次，然后每 24 小时运行一次
- **手动触发**：管理员可调用 POST /api/logs/cleanup 立即执行清理

**安全设计：**

- 后台循环的每次 run_cleanup() 后显式调用 session.commit() 确保删除持久化
- 异常捕获后不影响主应用，仅记 WARNING 日志
- 无日志可清理时跳过 INFO 日志，避免每日重复无意义输出

### 会话池管理

```
SessionPool（模块级单例，全 async）
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
        ├── asyncio.Lock      保护 clients 字典（协程安全）
        ├── asyncio.Lock      序列化签到批次（协程安全）
        └── asyncio.Semaphore 控制并发请求数（默认 5）
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
    │       │          ├── OpenCV WeChat QR 解码（主引擎）
    │       │          │       │
    │       │          │       ├── 成功 → 匹配域名参数 → 自动签到
    │       │          │       └── 失败 → ZXing WASM 备用解码
    │       │          │                  │
    │       │          │                  └── 匹配域名参数 → 自动签到
    │       │          │
    │       │          └── 轮询超时 → 停止扫描，提示重试
    │       │
    │       └── 失败 → 提示"使用拍照扫描"
    │
    └── 拍照扫描（降级方案）
              │
              ├── 调用系统相机（<input capture="environment">）
              ├── 加载照片 → canvas 绘制 → OpenCV WeChat QR 解码
              │                            │
              │                            └── 失败 → ZXing 备用解码
              └── 解析成功 → 自动签到
```

## 安全特性

| 措施                       | 实现                                                                   |
| -------------------------- | ---------------------------------------------------------------------- |
| **密码哈希**               | Argon2 via Passlib — 慢哈希抗暴力破解                                  |
| **JWT 签名**               | HS256/RS*/ES* 可选，密钥至少 16 字符                                   |
| **Refresh Token Rotation** | 每次刷新使旧 token 失效，防止泄露后重放                                |
| **Token 吊销**             | 登出时将 `jti` 加入 Redis 黑名单，TTL 自动过期                         |
| **速率限制**               | Redis 滑动窗口 — 登录/注册 5次/分钟，签到 60次/分钟                    |
| **Redis 熔断**             | 断路器模式，5min 健康检查间隔自动恢复；3s 短超时防连接卡死              |
| **凭据加密**               | Fernet (AES-128-CBC + HMAC) 加密课堂派密码，**启动时强制校验密钥存在** |
| **登录业务校验**           | 检查 API 返回 status 及 token，拒绝业务级失败（如密码过期）            |
| **状态追踪**               | 每个账号记录 `status_message`，失败原因可追溯                          |
| **密码强度**               | 8-128 字符，必须包含大小写字母和数字                                   |
| **CORS**                   | `ALLOWED_ORIGINS` 白名单机制，可配置多个来源                           |
| **SQL 注入防护**           | SQLModel 参数化查询                                                    |
| **客户端 IP 透传**         | 签到请求自动提取客户端真实 IP，以 `X-Forward-For` 透传至课堂派 API     |
| **异常处理**               | 全局异常处理器，敏感信息不暴露                                         |

## 项目结构

```
CheckInHelper/
├── main.py                 # 🔵 入口 — uvicorn 启动
├── pyproject.toml          # 📦 依赖管理（uv）
├── .env.example            # 🔧 环境变量模板
├── Dockerfile              # 🐳 Docker 多阶段构建
├── docker-compose.yml      # 🐳 一键启动 (MySQL + Redis + App)
├── favicon.ico             # 🖼️ 网站图标
├── CLAUDE.md               # 🤖 Claude Code 项目指令
│
├── app/                    # 🧩 后端核心
│   ├── main.py             # FastAPI 应用、中间件、异常处理、路由注册
│   ├── models.py           # SQLModel 数据模型定义
│   ├── deps.py             # 共享 FastAPI 依赖（get_current_user 等）
│   ├── utils.py            # 工具函数（RateLimiter、分页、IP 检测）
│   ├── login.html          # 独立登录/注册页面
│   ├── core/               # ⚙️ 核心基础设施
│   │   ├── api.py          # 课堂派第三方 API 客户端（httpx 异步）
│   │   ├── settings.py     # 集中配置（pydantic-settings，读取 .env）
│   │   ├── security.py     # 密码哈希 · JWT 签发 · 凭据加密
│   │   ├── sessions.py     # 会话池（异步签到 · 并发限流）
│   │   ├── watcher.py      # 自动签到观察器（轮询 + 执行）
│   │   ├── log_cleanup.py  # 签到日志清理（过期删除 + 超限删除）
│   │   ├── schema_sync.py  # SchemaSync 自动 schema 同步引擎（diff/备份/DDL/审计/并发锁）
│   │   └── db.py           # MySQL + Redis 连接池（断路器模式）
│   ├── routers/            # 🧭 领域路由模块（替代单文件巨石）
│   │   ├── auth.py         # 注册 / 登录 / 登出 / 刷新令牌
│   │   ├── user.py         # 用户 CRUD + 修改密码
│   │   ├── account.py      # 课堂派账号 CRUD + 验证 + 级联删除
│   │   ├── course.py       # 课程 CRUD + 课程绑定 CRUD
│   │   ├── checkin.py      # 批量签到执行
│   │   ├── invite_code.py  # 邀请码 CRUD
│   │   ├── log.py          # 签到日志列表 / 详情 / 删除
│   │   └── settings.py     # 系统设置（邀请码开关）
│   └── index.html          # 前端 SPA 模板
│
├── static/                 # 🎨 前端资源（本地化，无 CDN）
│   ├── common.css          # 公共样式（全局重置、表单字段、密码切换）
│   ├── login.css           # 登录页专用样式
│   ├── login.js            # 登录/注册 Vue 应用逻辑
│   ├── index.css           # 主应用样式（侧栏、表格、签到、扫码）
│   ├── index.js            # 主应用逻辑（Vue 3 Composition API）
│   ├── mdui.css            # MDUI 2 组件样式
│   ├── mdui.global.js      # MDUI 2 脚本
│   ├── vue.global.prod.js  # Vue 3 运行时
│   ├── material-icons.css  # Material Icons 样式
│   ├── MaterialIcons-Regular.ttf  # 图标字体
│   ├── img(32).webp        # 背景图（主页）
│   ├── img(64).webp        # 背景图（登录页）
│   ├── opencv.js           # OpenCV.js — WeChat QR 解码引擎
│   ├── wechat_qrcode_files.js  # WeChat QR 模型脚本
│   ├── wechat_qrcode_files.data # WeChat QR 模型数据
│   ├── zxing.min.js        # ZXing WASM 备用 QR 解码
│   └── test.html           # QR 解码测试页
│
├── tests/                  # ✅ 测试（360+ 个，覆盖核心模块 + 路由 + 基准测试）
│   ├── conftest.py         # 共享 Fixtures + benchmark 收集器
│   ├── test_security.py    # 密码哈希 · JWT · Fernet 加密 · 令牌黑名单
│   ├── test_models.py      # Pydantic/SQLModel 模型 · _extract_gps · is_position_error
│   ├── test_utils.py       # get_client_ip · RateLimiter · _in_time_windows 等
│   ├── test_db.py          # _RedisWrapper 断路器 · check_redis_health
│   ├── test_schema_sync.py # SchemaSync 全流程测试（119 个：数据类/diff/DDL/备份/幂等/
│   │                       # 默认对齐/BOOLEAN 规范化/排列积迁移路径/历史阶段/未来变更预测）
│   ├── test_log_cleanup.py # 签到日志清理（过期/超限清理函数测试）
│   └── routers/
│       ├── __init__.py
│       ├── test_auth.py              # 注册/登录/登出/令牌刷新
│       ├── test_benchmark_checkin.py # 签到链路延迟基准测试 (median<50ms)
│       ├── test_log.py              # 签到日志管理 API 集成测试
│       └── ...                       # 更多路由集成测试
│
├── scripts/                # 🛠️ 工具脚本
│   └── backfill_accounts.py  # 补齐旧账号用户详情字段
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
