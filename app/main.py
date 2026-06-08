import os
import re
from pathlib import Path
from datetime import datetime, timezone
from starlette.exceptions import HTTPException
from fastapi import FastAPI, Request, Response, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from app.security import (
    configure_jwt,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db import Session, Redis, ConnectionError, get_session_with, get_redis
from sqlmodel import select
from app.models import *
from app.api import CheckInRequest

import logging

logger = logging.getLogger(__name__)

configure_jwt(
    os.environ.get("JWT_SECRET", "secret-" + "-".join(__file__)),
    os.environ.get("JWT_ALGORITHM", "HS256"),
    os.environ.get("JWT_EXPIRE_HOURS") or 24 * 7,
)

# 创建 FastAPI 实例
app = FastAPI(
    title="CheckInHelper API",
    description="CheckInHelper",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================
#            异常处理
# ================================


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error(f"HTTP 异常：{str(exc)}", exc_info=True)
    import traceback

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            code=exc.status_code,
            message=exc.detail,
            detail=traceback.format_exc() if app.debug else None,
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
            detail=str(exc) if app.debug else None,
        ).model_dump(exclude_none=True),
    )


# ================================
#               依赖
# ================================


def get_user_cache(redis: Redis, user_id: int) -> User | None:
    try:
        user_cache = redis.get(f"user:{user_id}")
        if user_cache is None:
            return None
        return User.model_validate(user_cache)
    except ConnectionError:
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

    user_cache = get_user_cache(redis, user_id)
    if user_cache is not None:
        return user_cache

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    redis.set(f"user:{user_id}", user.model_dump(exclude_none=True), 86400)
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


@app.post("/api/register")
async def register(
    email: str = Body(...),
    password: str = Body(...),
    session: Session = Depends(get_session_with),
):
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少为 8 个字符")
    if not re.match(r"^[a-zA-Z0-9_-]+$", password):
        raise HTTPException(
            status_code=400, detail="密码只能包含字母、数字、下划线、减号"
        )

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
):
    if not re.match(r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式错误")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少为 8 个字符")
    if not re.match(r"^[a-zA-Z0-9_-]+$", password):
        raise HTTPException(
            status_code=400, detail="密码只能包含字母、数字、下划线、减号"
        )

    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    if not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="密码错误")

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


# ================================
#          User CRUD
# ================================


@app.get("/api/users", response_model=list[User])
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


@app.get("/api/users/{user_id}", response_model=User)
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


@app.post("/api/users", response_model=User)
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


@app.put("/api/users/{user_id}", response_model=User)
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
        user.password = hash_password(password)

    if role is not None and current_user.role == Role.admin:
        user.role = role

    if is_active is not None:
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


@app.get("/api/accounts", response_model=list[Account])
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
        return []

    accounts = session.exec(select(Account).where(Account.id.in_(account_ids))).all()

    return BaseResponse(
        message="success",
        data=[account.model_dump(exclude=["password"]) for account in accounts],
    )


@app.get("/api/accounts/{account_id}", response_model=Account)
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


@app.post("/api/accounts", response_model=Account)
async def create_account(
    email: str = Body(...),
    password: str = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """创建新账号并关联到当前用户"""
    # 检查邮箱是否已存在
    existing_account = session.exec(
        select(Account).where(Account.email == email)
    ).first()

    if existing_account:
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    account = Account(
        email=email,
        password=hash_password(password),
    )
    session.add(account)
    session.flush()

    # 创建用户与账号的关联
    user_account = UserAccount(
        user_id=current_user.id,
        account_id=account.id,
    )
    session.add(user_account)
    session.flush()

    return BaseResponse(
        message="success",
        data=account.model_dump(exclude=["password"]),
    )


@app.put("/api/accounts/{account_id}", response_model=Account)
async def update_account(
    account_id: int,
    email: Optional[str] = Body(None),
    password: Optional[str] = Body(None),
    is_active: Optional[bool] = Body(None),
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
        account.password = hash_password(password)

    if is_active is not None:
        account.is_active = is_active

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
        session.delete(user_accounts)
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


@app.get("/api/courses/bindings", response_model=list[CourseBinding])
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
        return []

    # 查询这些账号的课程绑定
    bindings = session.exec(
        select(CourseBinding).where(CourseBinding.account_id.in_(account_ids))
    ).all()

    return BaseResponse(message="success", data=[x.model_dump() for x in bindings])


@app.post("/api/courses/bindings", response_model=CourseBinding)
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

    return BaseResponse(code=200, message="删除成功")


# ================================
#        CheckInLog CRUD
# ================================


@app.get("/api/checkin/logs", response_model=list[CheckInLog])
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
        return []

    query = select(CheckInLog).where(CheckInLog.account_id.in_(account_ids))

    if account_id is not None:
        query = query.where(CheckInLog.account_id == account_id)

    if course_id is not None:
        query = query.where(CheckInLog.course_id == course_id)

    logs = session.exec(query).all()
    return BaseResponse(message="success", data=[x.model_dump() for x in logs])


# @app.post("/api/checkin/logs", response_model=CheckInLog)
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


@app.get("/api/checkin/logs/{log_id}", response_model=CheckInLog)
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


@app.put("/api/checkin/logs/{log_id}", response_model=CheckInLog)
async def update_checkin_log(
    log_id: int,
    status: int = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """更新签到日志状态"""
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
        raise HTTPException(status_code=403, detail="无权限修改此日志")

    log.status = status
    session.add(log)
    session.flush()

    return BaseResponse(message="success", data=log.model_dump())


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
):
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

    if not session_pool.create(accounts):
        return BaseResponse(code=500, message="创建会话失败")

    account_ids = [a.id for a in accounts]
    result = session_pool.execute_checkin(current_user.id, account_ids, data)
    session_pool.remove(account_ids)

    success_count = sum(r[1].success for r in result)
    return BaseResponse(
        code=200,
        data={
            "success_count": success_count,
            "results": [r[1].model_dump() for r in result],
        },
        message=f"成功签到{success_count}个账号",
    )
