import os
import re
import json
import time
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone
from starlette.exceptions import HTTPException
from fastapi import FastAPI, Request, Response, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.security import (
    configure_jwt,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
    validate_password_strength,
    is_token_blacklisted,
    encrypt_credential,
)
from app.db import Session, Redis, ConnectionError, get_session_with, get_redis
from sqlmodel import select
from app.models import *
from app.api import CheckInRequest, CheckInResult

import logging

logger = logging.getLogger(__name__)

# JWT 配置 — 生产环境务必设置 JWT_SECRET 环境变量
_jwt_secret = os.environ.get("JWT_SECRET")
if _jwt_secret is None:
    _jwt_secret = secrets.token_hex(32)
    logger.warning("JWT_SECRET 未设置！已生成随机密钥。重启后所有 token 将失效。")
configure_jwt(
    _jwt_secret,
    os.environ.get("JWT_ALGORITHM", "HS256"),
    os.environ.get("JWT_EXPIRE_HOURS") or 24 * 7,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库并清空 user 缓存。"""
    from app.db import init_db, get_redis_client

    init_db()
    r = get_redis_client()
    try:
        keys: list[bytes] = []
        cursor = "0"
        while True:
            cursor, batch = r.scan(cursor=cursor, match="user:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        if keys:
            r.delete(*keys)
            logger.info("已清除 %s 条 user 缓存", len(keys))
    except Exception:
        logger.warning("清除 user 缓存失败（Redis 可能不可用）")
    yield
    # shutdown — nothing to do


# 创建 FastAPI 实例
app = FastAPI(
    title="CheckInHelper API",
    description="CheckInHelper",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# 添加 CORS 中间件
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS")
if ALLOWED_ORIGINS:
    _origins = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
else:
    _origins = []  # 默认不允许任何跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

# 挂载静态文件目录
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ================================
#            异常处理
# ================================


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error(f"HTTP 异常：{str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            code=exc.status_code,
            message=exc.detail,
        ).model_dump(exclude_none=True),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"未处理的异常：{str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code=500,
            message="Internal Server Error",
        ).model_dump(exclude_none=True),
    )


# ================================
#            速率限制
# ================================


class RateLimiter:
    """Redis-based rate limiter dependency."""

    def __init__(self, times: int, seconds: int):
        self.times = times
        self.seconds = seconds

    async def __call__(self, request: Request, redis: Redis = Depends(get_redis)):
        client_ip = request.client.host if request.client else "unknown"
        key = f"rate_limit:{request.url.path}:{client_ip}"
        try:
            current = redis.incr(key)
            if current == 1:
                redis.expire(key, self.seconds)
        except ConnectionError:
            return  # Redis 不可用时放行
        if current > self.times:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


# 登录/注册接口每分钟最多 5 次请求
auth_rate_limiter = RateLimiter(times=5, seconds=60)


# ================================
#               依赖
# ================================


def get_user_cache(redis: Redis, user_id: int) -> User | None:
    try:
        user_cache = redis.get(f"user:{user_id}")
        if user_cache is None:
            return None
        return User.model_validate(json.loads(user_cache))
    except (ConnectionError, json.JSONDecodeError, Exception):
        logger.warning("Redis unavailable — user cache miss for %s", user_id)
        return None


def get_current_user(
    request: Request,
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
) -> User | None:
    authorization = request.headers.get("Authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺失令牌")

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="认证令牌格式错误")

    # 检查 token 是否已被吊销
    token_jti = payload.get("jti")
    if token_jti and is_token_blacklisted(token_jti, redis):
        raise HTTPException(status_code=401, detail="令牌已被吊销")

    user_cache = get_user_cache(redis, user_id)
    if user_cache is not None:
        # 缓存命中时也检查 is_active
        if not user_cache.is_active:
            raise HTTPException(status_code=403, detail="账号已被禁用")
        return user_cache

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    redis.set(
        f"user:{user_id}",
        json.dumps(user.model_dump(exclude_none=True, mode="json")),
        86400,
    )
    return user


# ================================
#               路由
# ================================


@app.get("/")
async def root():
    path = Path(__file__).parent / "index.html"
    return FileResponse(path)


@app.head("/")
async def root_head():
    return Response(status_code=200)


@app.get("/favicon.ico")
async def favicon():
    path = Path(__file__).parent.parent / "favicon.ico"
    return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


@app.post("/api/register")
async def register(
    email: str = Body(...),
    password: str = Body(...),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(auth_rate_limiter),
):
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")
    valid, msg = validate_password_strength(password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    user = session.exec(select(User).where(User.email == email)).first()
    if user is not None:
        raise HTTPException(status_code=400, detail="用户已存在")

    user = User(
        email=email,
        password=hash_password(password),
    )
    session.add(user)
    session.flush()
    session.refresh(user)

    access_token = create_access_token(str(user.id))
    return BaseResponse(
        message="注册成功",
        data={
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
            },
        },
    )


@app.post("/api/login")
async def login(
    email: str = Body(...),
    password: str = Body(...),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(auth_rate_limiter),
):
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少为 8 个字符")

    user = session.exec(select(User).where(User.email == email)).first()
    if user is None or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.flush()

    access_token = create_access_token(str(user.id))
    return BaseResponse(
        code=200,
        message="登录成功",
        data={
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
            },
        },
    )


@app.post("/api/logout")
async def logout(
    request: Request,
    redis: Redis = Depends(get_redis),
) -> BaseResponse:
    """登出 — 将当前 token 加入黑名单"""
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = decode_access_token(token)
        if payload and payload.get("jti"):
            jti = payload["jti"]
            exp = payload.get("exp", 0)
            ttl = max(int(exp - time.time()), 60)  # 剩余有效期，至少 60 秒
            from app.security import blacklist_token

            blacklist_token(jti, redis, ttl=ttl)
    return BaseResponse(message="已登出")


# ================================
#          User CRUD
# ================================


@app.get("/api/users")
async def list_users(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取所有用户列表（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")

    users = session.exec(select(User)).all()
    return BaseResponse(
        message="success",
        data=[user.model_dump(exclude=["password"]) for user in users],
    )


@app.get("/api/users/{user_id}")
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取指定用户信息"""
    if current_user.role != Role.admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="权限不足")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    return BaseResponse(
        message="success",
        data=user.model_dump(exclude=["password"]),
    )


@app.post("/api/users")
async def create_user(
    email: str = Body(...),
    password: str = Body(...),
    role: Role = Body(default=Role.user),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """创建新用户（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")

    # 验证邮箱格式
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")

    # 验证密码强度
    valid, msg = validate_password_strength(password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 检查邮箱是否已存在
    existing_user = session.exec(select(User).where(User.email == email)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    user = User(
        email=email,
        password=hash_password(password),
        role=role,
    )
    session.add(user)
    session.flush()

    return BaseResponse(
        message="success",
        data=user.model_dump(exclude=["password"]),
    )


@app.put("/api/users/{user_id}")
async def update_user(
    user_id: int,
    email: Optional[str] = Body(None),
    password: Optional[str] = Body(None),
    role: Optional[Role] = Body(None),
    is_active: Optional[bool] = Body(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    """更新用户信息"""
    if current_user.role != Role.admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="权限不足")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if email is not None:
        # 检查新邮箱是否已被其他用户使用
        existing_user = session.exec(
            select(User).where(User.email == email, User.id != user_id)
        ).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="邮箱已被使用")
        user.email = email

    if password is not None:
        valid, msg = validate_password_strength(password)
        if not valid:
            raise HTTPException(status_code=400, detail=msg)
        user.password = hash_password(password)

    if role is not None and current_user.role == Role.admin:
        user.role = role

    if is_active is not None and current_user.role == Role.admin:
        user.is_active = is_active

    session.add(user)
    session.flush()

    # 清除缓存
    redis.delete(f"user:{user_id}")

    return BaseResponse(
        message="success",
        data=user.model_dump(exclude=["password"]),
    )


@app.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    """删除用户（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")

    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    session.delete(user)
    session.flush()

    # 清除缓存
    redis.delete(f"user:{user_id}")

    return BaseResponse(message="删除成功")


# ================================
#          Account CRUD
# ================================


@app.get("/api/accounts")
async def list_accounts(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取当前用户的所有账号"""
    # 通过 UserAccount 关联查询
    user_accounts = session.exec(
        select(UserAccount).where(UserAccount.user_id == current_user.id)
    ).all()

    account_ids = [ua.account_id for ua in user_accounts]
    if not account_ids:
        return BaseResponse(message="success", data=[])

    accounts = session.exec(select(Account).where(Account.id.in_(account_ids))).all()

    return BaseResponse(
        message="success",
        data=[account.model_dump(exclude=["password"]) for account in accounts],
    )


@app.get("/api/accounts/{account_id}")
async def get_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取指定账号信息"""
    # 验证权限：确保账号属于当前用户
    user_account = session.exec(
        select(UserAccount).where(
            UserAccount.user_id == current_user.id, UserAccount.account_id == account_id
        )
    ).first()

    if user_account is None:
        raise HTTPException(status_code=404, detail="账号不存在或无权限访问")

    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    return BaseResponse(
        message="success",
        data=account.model_dump(exclude=["password"]),
    )


@app.post("/api/accounts")
async def create_account(
    email: str = Body(...),
    password: str = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
):
    """创建新账号并关联到当前用户"""
    # 1. 检查是否已存在
    existing = session.exec(select(Account).where(Account.email == email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="该账号已存在")

    # 2. 先通过课堂派 API 验证凭据有效性（不入库）
    from app.api import KetangPaiAPI

    uid = None
    token = None
    try:
        client = KetangPaiAPI(email, password)
        response = client.login()
        token = response.data.token
        uid = response.data.uid
        client.close()
        if not token:
            raise HTTPException(status_code=400, detail="账号验证失败")
    except Exception as e:
        logger.warning("Account verification failed for %s: %s", email, e)
        raise HTTPException(status_code=400, detail=f"账号验证失败：{e}")

    # 3. 验证通过再入库
    account = Account(
        email=email,
        password=encrypt_credential(password),
        uid=uid,
        status=1,
    )
    session.add(account)
    session.flush()
    redis.set(f"account:{account.id}:token", token)

    user_account = UserAccount(
        user_id=current_user.id,
        account_id=account.id,
    )
    session.add(user_account)
    session.flush()

    # 4. 加入会话池
    import asyncio
    from app.sessions import session_pool

    await asyncio.to_thread(session_pool.create, [account], False)

    # 5. 拉取课程列表并自动绑定（不启用）
    try:
        courses = await asyncio.to_thread(
            session_pool.get_course_list, account.id
        )
        if courses:
            for course_data in courses:
                # 检查课程是否已存在，不存在则创建
                course = session.get(Course, course_data["id"])
                if course is None:
                    course = Course(
                        id=course_data["id"],
                        code=course_data.get("code", ""),
                        course_name=course_data.get("course_name", ""),
                        semester=course_data.get("semester", ""),
                        term=course_data.get("term", ""),
                    )
                    session.add(course)
                    session.flush()

                # 检查是否已有绑定
                existing_binding = session.exec(
                    select(CourseBinding).where(
                        CourseBinding.course_id == course.id,
                        CourseBinding.account_id == account.id,
                    )
                ).first()
                if existing_binding is None:
                    session.add(
                        CourseBinding(
                            course_id=course.id,
                            account_id=account.id,
                            is_active=False,  # 默认不启用
                        )
                    )
            session.flush()
    except Exception as e:
        logger.warning("Failed to fetch courses for account %s: %s", account.id, e)

    return BaseResponse(
        message="success",
        data=account.model_dump(exclude=["password"]),
    )


@app.put("/api/accounts/{account_id}")
async def update_account(
    account_id: int,
    email: Optional[str] = Body(None),
    password: Optional[str] = Body(None),
    status: Optional[int] = Body(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """更新账号信息"""
    # 验证权限
    user_account = session.exec(
        select(UserAccount).where(
            UserAccount.user_id == current_user.id, UserAccount.account_id == account_id
        )
    ).first()

    if user_account is None:
        raise HTTPException(status_code=404, detail="账号不存在或无权限访问")

    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    if email is not None:
        # 检查新邮箱是否已被其他账号使用
        existing_account = session.exec(
            select(Account).where(Account.email == email, Account.id != account_id)
        ).first()
        if existing_account:
            raise HTTPException(status_code=400, detail="邮箱已被使用")
        account.email = email

    if password is not None:
        account.password = encrypt_credential(password)  # 课堂派 API 凭据加密存储

    if status is not None:
        account.status = status

    session.add(account)
    session.flush()

    return BaseResponse(
        message="success",
        data=account.model_dump(exclude=["password"]),
    )


@app.delete("/api/accounts/{account_id}")
async def delete_account(
    account_id: int,
    admin: bool = False,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """删除账号"""
    if admin:
        # 验证管理员权限
        if not current_user or current_user.role != Role.admin:
            raise HTTPException(status_code=403, detail="无权限")

        account = session.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")

        user_accounts = session.exec(
            select(UserAccount).where(UserAccount.account_id == account_id)
        ).all()

        relation = len(user_accounts)

        # 先删除关联关系
        for ua in user_accounts:
            session.delete(ua)
        # 再删除账号
        session.delete(account)
        session.flush()

        return BaseResponse(code=200, message="删除成功", data={"relation": relation})
    else:
        # 验证权限
        user_account = session.exec(
            select(UserAccount).where(
                UserAccount.user_id == current_user.id,
                UserAccount.account_id == account_id,
            )
        ).first()

        if user_account is None:
            raise HTTPException(status_code=404, detail="账号不存在或无权限访问")

        account = session.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")

        # 先删除关联关系
        session.delete(user_account)

        # 验证账号是否被其他用户关联
        user_account = session.exec(
            select(UserAccount).where(UserAccount.account_id == account_id)
        ).first()
        if user_account is None:
            # 账号未被其他用户关联，可以删除
            session.delete(account)
        session.flush()

        return BaseResponse(code=200, message="删除成功")


# ================================
#       CourseBinding CRUD
# ================================


@app.get("/api/courses/bindings")
async def list_course_bindings(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取当前用户的所有课程绑定"""
    # 先获取用户的所有账号
    user_accounts = session.exec(
        select(UserAccount).where(UserAccount.user_id == current_user.id)
    ).all()

    account_ids = [ua.account_id for ua in user_accounts]
    if not account_ids:
        return BaseResponse(message="success", data=[])

    # 查询这些账号的课程绑定
    bindings = session.exec(
        select(CourseBinding).where(CourseBinding.account_id.in_(account_ids))
    ).all()

    return BaseResponse(message="success", data=[x.model_dump() for x in bindings])


@app.post("/api/courses/bindings")
async def create_course_binding(
    course_id: str = Body(...),
    account_id: int = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """创建课程绑定"""
    # 验证账号是否属于当前用户
    user_account = session.exec(
        select(UserAccount).where(
            UserAccount.user_id == current_user.id, UserAccount.account_id == account_id
        )
    ).first()

    if user_account is None:
        raise HTTPException(status_code=404, detail="账号不存在或无权限访问")

    # 检查是否已存在相同的绑定
    existing_binding = session.exec(
        select(CourseBinding).where(
            CourseBinding.course_id == course_id, CourseBinding.account_id == account_id
        )
    ).first()

    if existing_binding:
        raise HTTPException(status_code=400, detail="课程绑定已存在")

    binding = CourseBinding(
        course_id=course_id,
        account_id=account_id,
    )
    session.add(binding)
    session.flush()

    return BaseResponse(message="success", data=binding.model_dump())


@app.delete("/api/courses/bindings/{binding_id}")
async def delete_course_binding(
    binding_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """删除课程绑定"""
    binding = session.get(CourseBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="课程绑定不存在")

    # 验证权限：确保账号属于当前用户
    user_account = session.exec(
        select(UserAccount).where(
            UserAccount.user_id == current_user.id,
            UserAccount.account_id == binding.account_id,
        )
    ).first()

    if user_account is None:
        raise HTTPException(status_code=403, detail="无权限删除此绑定")

    session.delete(binding)
    session.flush()

    # 检查该课程是否还有其它绑定，引用数归零则删除课程
    remaining = session.exec(
        select(CourseBinding).where(
            CourseBinding.course_id == binding.course_id,
        )
    ).first()
    if remaining is None:
        course = session.get(Course, binding.course_id)
        if course is not None:
            session.delete(course)

    return BaseResponse(code=200, message="删除成功")


@app.put("/api/courses/bindings/{binding_id}")
async def update_course_binding(
    binding_id: int,
    is_active: bool = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """切换课程绑定的启用状态"""
    binding = session.get(CourseBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="课程绑定不存在")

    # 验证权限
    user_account = session.exec(
        select(UserAccount).where(
            UserAccount.user_id == current_user.id,
            UserAccount.account_id == binding.account_id,
        )
    ).first()
    if user_account is None:
        raise HTTPException(status_code=403, detail="无权限修改此绑定")

    binding.is_active = is_active
    session.add(binding)
    session.flush()

    return BaseResponse(message="success", data=binding.model_dump())


# ================================
#       Course CRUD
# ================================


@app.get("/api/courses")
async def list_courses(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """列出所有课程（管理员），或用户关联的课程"""
    if current_user.role == Role.admin:
        courses = session.exec(select(Course)).all()
    else:
        user_accounts = session.exec(
            select(UserAccount).where(UserAccount.user_id == current_user.id)
        ).all()
        account_ids = [ua.account_id for ua in user_accounts]
        if not account_ids:
            return BaseResponse(message="success", data=[])
        courses = session.exec(
            select(Course)
            .join(CourseBinding)
            .where(CourseBinding.account_id.in_(account_ids))
            .distinct()
        ).all()

    return BaseResponse(
        message="success",
        data=[c.model_dump() for c in courses],
    )


@app.get("/api/courses/{course_id}")
async def get_course(
    course_id: str,
    session: Session = Depends(get_session_with),
):
    """获取指定课程信息"""
    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    return BaseResponse(message="success", data=course.model_dump())


@app.delete("/api/courses/{course_id}")
async def delete_course(
    course_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """删除课程（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")

    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="课程不存在")

    # 先删关联绑定
    bindings = session.exec(
        select(CourseBinding).where(CourseBinding.course_id == course_id)
    ).all()
    for b in bindings:
        session.delete(b)

    session.delete(course)
    session.flush()

    return BaseResponse(code=200, message="删除成功")


# ================================
#        CheckInLog CRUD
# ================================


@app.get("/api/checkin/logs")
async def list_checkin_logs(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    account_id: Optional[int] = None,
    course_id: Optional[str] = None,
):
    """获取签到日志列表"""
    # 获取用户的所有账号
    user_accounts = session.exec(
        select(UserAccount).where(UserAccount.user_id == current_user.id)
    ).all()

    account_ids = [ua.account_id for ua in user_accounts]
    if not account_ids:
        return BaseResponse(message="success", data=[])

    query = select(CheckInLog).where(CheckInLog.account_id.in_(account_ids))

    if account_id is not None:
        query = query.where(CheckInLog.account_id == account_id)

    if course_id is not None:
        query = query.where(CheckInLog.course_id == course_id)

    logs = session.exec(query).all()
    return BaseResponse(message="success", data=[x.model_dump() for x in logs])


# @app.post("/api/checkin/logs")
# async def create_checkin_log(
#     account_id: int = Body(...),
#     course_id: str = Body(...),
#     status: int = Body(default=0),
#     current_user: User = Depends(get_current_user),
#     session: Session = Depends(get_session_with),
# ):
#     """创建签到日志"""
#     # 验证账号是否属于当前用户
#     user_account = session.exec(
#         select(UserAccount).where(
#             UserAccount.user_id == current_user.id, UserAccount.account_id == account_id
#         )
#     ).first()

#     if user_account is None:
#         raise HTTPException(status_code=404, detail="账号不存在或无权限访问")

#     log = CheckInLog(
#         account_id=account_id,
#         course_id=course_id,
#         status=status,
#     )
#     session.add(log)
#     session.flush()

#     return BaseResponse(message="success", data=log.model_dump())


@app.get("/api/checkin/logs/{log_id}")
async def get_checkin_log(
    log_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """获取指定签到日志"""
    log = session.get(CheckInLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="签到日志不存在")

    # 验证权限
    user_account = session.exec(
        select(UserAccount).where(
            UserAccount.user_id == current_user.id,
            UserAccount.account_id == log.account_id,
        )
    ).first()

    if user_account is None:
        raise HTTPException(status_code=403, detail="无权限访问此日志")

    return BaseResponse(message="success", data=log.model_dump())


# @app.put("/api/checkin/logs/{log_id}")
# async def update_checkin_log(
#     log_id: int,
#     status: int = Body(...),
#     current_user: User = Depends(get_current_user),
#     session: Session = Depends(get_session_with),
# ):
#     """更新签到日志状态"""
#     log = session.get(CheckInLog, log_id)
#     if log is None:
#         raise HTTPException(status_code=404, detail="签到日志不存在")

#     # 验证权限
#     user_account = session.exec(
#         select(UserAccount).where(
#             UserAccount.user_id == current_user.id,
#             UserAccount.account_id == log.account_id,
#         )
#     ).first()

#     if user_account is None:
#         raise HTTPException(status_code=403, detail="无权限修改此日志")

#     log.status = status
#     session.add(log)
#     session.flush()

#     return BaseResponse(message="success", data=log.model_dump())


@app.delete("/api/checkin/logs/{log_id}")
async def delete_checkin_log(
    log_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """删除签到日志"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="无删除权限")

    log = session.get(CheckInLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="签到日志不存在")

    session.delete(log)
    session.flush()

    return BaseResponse(code=200, message="删除成功")


# ================================
#             CheckIn
# ================================


@app.post("/api/checkin")
async def check_in(
    data: CheckInRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    _rate_limit: None = Depends(RateLimiter(times=10, seconds=60)),
):
    import asyncio
    from app.sessions import session_pool

    course_id = data.courseid
    accounts = session.exec(
        select(Account)
        .join(UserAccount)
        .join(CourseBinding)
        .where(
            UserAccount.user_id == current_user.id,
            CourseBinding.course_id == course_id,
            CourseBinding.is_active == True,
        )
    ).all()
    if not accounts:
        raise HTTPException(status_code=404, detail="无绑定此课程的账号")

    if not await asyncio.to_thread(session_pool.create, accounts):
        return BaseResponse(code=500, message="创建会话失败")

    account_ids = [a.id for a in accounts]
    result: dict[int, CheckInResult | None] = await session_pool.execute_checkin(
        current_user.id, account_ids, data
    )

    success_count = sum(1 for r in result.values() if r is not None and r.success)
    return BaseResponse(
        code=200,
        data={
            "success_count": success_count,
            "results": [r.model_dump() for r in result.values() if r is not None],
        },
        message=f"成功签到{success_count}个账号",
    )
