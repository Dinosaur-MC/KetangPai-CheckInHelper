from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Response, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware
from sqlmodel import select


from app.core.db import Session, get_session_with
from app.core.settings import settings
from app.deps import get_current_user
from app.utils import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate

from app.models import PaginatedResponse, ErrorResponse, Account, User, Role

import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库、清空缓存、启动自动签到观察器。"""
    from app.core.db import init_db, get_redis_client
    from app.core.watcher import auto_checkin_watcher

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

    # 启动自动签到观察器
    await auto_checkin_watcher.start()

    yield

    # 关闭观察器
    await auto_checkin_watcher.stop()


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
if settings.allowed_origins:
    _origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
else:
    _origins = []  # 默认不允许任何跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

# GZip 压缩 — 对 opencv.js (~6.6MB) 尤其重要，可压缩至 ~2.2MB
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 挂载静态文件目录
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ================================
#            异常处理
# ================================


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error("HTTP 异常：%s", exc, exc_info=True)
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            code=exc.status_code,
            message=exc.detail,
        ).model_dump(exclude_none=True),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("未处理的异常：%s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code=500,
            message="Internal Server Error",
        ).model_dump(exclude_none=True),
    )


# ================================
#               路由
# ================================


@app.get("/")
async def root(request: Request):
    if not request.cookies.get("access_token"):
        return RedirectResponse(url="/login")
    path = Path(__file__).parent / "index.html"
    return FileResponse(path)


@app.get("/login")
async def login_page():
    path = Path(__file__).parent / "login.html"
    return FileResponse(path)


@app.head("/")
async def root_head():
    return Response(status_code=200)


@app.get("/favicon.ico")
async def favicon():
    path = Path(__file__).parent.parent / "favicon.ico"
    return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/admin/accounts")
async def admin_list_accounts(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """查询所有账号信息（仅管理员）"""
    if current_user.role != Role.admin:
        raise HTTPException(status_code=403, detail="权限不足")
    query = select(Account).order_by(Account.created_at.desc())
    accounts, total = paginate(session, query, page, page_size)
    return PaginatedResponse(
        message="success",
        data=[a.model_dump(exclude=["password"]) for a in accounts],
        total=total,
        page=page,
        page_size=page_size,
    )


# ================================
#              子路由
# ================================

from .routers import (
    account_router,
    auth_router,
    checkin_router,
    course_router,
    invite_code_router,
    log_router,
    settings_router,
    user_router,
)

app.include_router(account_router)
app.include_router(auth_router)
app.include_router(checkin_router)
app.include_router(course_router)
app.include_router(invite_code_router)
app.include_router(log_router)
app.include_router(settings_router)
app.include_router(user_router)
