from typing import Optional

from redis import Redis
from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Body, Query
from sqlmodel import select

from app.deps import get_current_user
from app.core.db import Session, get_redis, get_session_with
from app.core.security import encrypt_credential
from app.utils import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate
from app.models import (
    BaseResponse,
    PaginatedResponse,
    CheckInLog,
    Account,
    Course,
    Role,
    User,
    UserAccount,
    CourseBinding,
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["Account"])

# ================================
#          Account CRUD
# ================================


@router.get("")
async def list_accounts(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """获取当前用户关联的账号"""
    query = (
        select(Account)
        .join(UserAccount)
        .where(UserAccount.user_id == current_user.id)
        .order_by(Account.created_at.desc())
    )
    accounts, total = paginate(session, query, page, page_size)
    return PaginatedResponse(
        message="success",
        data=[a.model_dump(exclude=["password"]) for a in accounts],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{account_id}")
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


@router.post("")
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
        # 账号已存在 — 直接关联到当前用户（不重复验证）
        account = existing
        # 检查当前用户是否已关联此账号
        existing_link = session.exec(
            select(UserAccount).where(
                UserAccount.user_id == current_user.id,
                UserAccount.account_id == account.id,
            )
        ).first()
        if existing_link:
            raise HTTPException(status_code=400, detail="该账号已关联到当前用户")
    else:
        # 2. 先通过课堂派 API 验证凭据有效性（不入库）
        from app.core.api import KetangPaiAPI

        uid = None
        token = None
        try:
            client = KetangPaiAPI(email, password)
            response = await client.login()
            token = response.data.token
            uid = response.data.uid
            await client.close()
            if not token:
                raise HTTPException(status_code=400, detail="账号验证失败")
        except RuntimeError as e:
            logger.warning("Account verification failed for %s: %s", email, e)
            raise HTTPException(status_code=400, detail=f"账号验证失败：{e}")
        except Exception as e:
            logger.warning("Account verification failed for %s: %s", email, e)
            raise HTTPException(status_code=400, detail="账号验证失败")

        # 3. 获取用户详细信息
        userinfo = None
        try:
            client = KetangPaiAPI(email, password, token)
            resp = await client.get_user_info()
            userinfo = resp.data
            await client.close()
        except Exception as e:
            logger.warning("Failed to get user info for %s: %s", email, e)

        # 4. 验证通过再入库
        account = Account(
            email=email,
            password=encrypt_credential(password),
            uid=uid,
            username=userinfo.username if userinfo else "",
            avatar=userinfo.avatar if userinfo else "",
            school=userinfo.school if userinfo else "",
            stno=userinfo.stno if userinfo else "",
            department=userinfo.department if userinfo else "",
            mobile=userinfo.mobile if userinfo else "",
            ktp_account=userinfo.account if userinfo else "",
            status=1,
        )
        session.add(account)
        session.flush()
        if redis:
            redis.set(f"account:{account.id}:token", token)

        # 4. 加入会话池
        from app.core.sessions import session_pool

        await session_pool.create([account], False)

    # 建立用户-账号关联
    user_account = UserAccount(
        user_id=current_user.id,
        account_id=account.id,
    )
    session.add(user_account)
    session.flush()

    # 5. 拉取课程列表并自动绑定（不启用）
    try:
        from app.core.sessions import session_pool

        courses = await session_pool.get_course_list(account.id)
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


@router.put("/{account_id}")
async def update_account(
    account_id: int,
    email: Optional[str] = Body(None),
    password: Optional[str] = Body(None),
    status: Optional[int] = Body(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    redis: Redis = Depends(get_redis),
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
        account.password = encrypt_credential(password)
        # 密码更新后重置状态，并尝试登录刷新用户详情
        account.status = 1
        account.status_message = ""
        try:
            from app.core.api import KetangPaiAPI

            client = KetangPaiAPI(
                email if email is not None else account.email, password
            )
            response = await client.login()
            token = response.data.token
            if not token:
                raise HTTPException(status_code=400, detail="账号验证失败")
            if redis:
                redis.set(f"account:{account.id}:token", token)
            try:
                resp = await client.get_user_info()
                info = resp.data
                account.username = info.username
                account.avatar = info.avatar
                account.school = info.school
                account.stno = info.stno
                account.department = info.department or ""
                account.mobile = info.mobile
                account.ktp_account = info.account
            except Exception:
                pass
            await client.close()
        except Exception as e:
            logger.warning("Password updated but re-login failed: %s", e)

    if status is not None:
        account.status = status

    session.add(account)
    session.flush()

    return BaseResponse(
        message="success",
        data=account.model_dump(exclude=["password"]),
    )


@router.post("/{account_id}/verify")
async def verify_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
):
    """重新验证课堂派账号凭据有效性"""
    # 验证权限
    user_account = session.exec(
        select(UserAccount).where(
            UserAccount.user_id == current_user.id,
            UserAccount.account_id == account_id,
        )
    ).first()
    if user_account is None and current_user.role != Role.admin:
        raise HTTPException(status_code=404, detail="账号不存在或无权限访问")

    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    from app.core.api import KetangPaiAPI
    from app.core.security import decrypt_credential

    password = decrypt_credential(account.password)
    try:
        client = KetangPaiAPI(account.email, password)
        await client.login()
        # 验证成功后顺便刷新用户详情
        try:
            resp = await client.get_user_info()
            info = resp.data
            account.username = info.username
            account.avatar = info.avatar
            account.school = info.school
            account.stno = info.stno
            account.department = info.department or ""
            account.mobile = info.mobile
            account.ktp_account = info.account
        except Exception as e:
            logger.warning("Failed to refresh user info for %s: %s", account.email, e)
        await client.close()
        account.status = 1
        account.status_message = ""
        session.add(account)
        session.flush()
        return BaseResponse(message="验证成功，账号正常")
    except Exception as e:
        msg = str(e) or "验证失败"
        account.status = -1
        account.status_message = msg
        session.add(account)
        session.flush()
        return BaseResponse(code=400, message=f"验证失败：{msg}")


def _cascade_delete_account(
    session: Session,
    account: Account,
    user_account: UserAccount | None = None,
    *,
    force: bool = False,
):
    """级联删除账号关联数据。

    :param session: 数据库会话
    :param account: 要删除的账号
    :param user_account: 当前用户的关联记录（None 时由函数自行查询全部关联）
    :param force: 管理员删除时强制删除账号本身（无视是否还有其他用户关联）
    """
    # 1. 删除关联的课程绑定（含无引用课程清理）
    bindings = session.exec(
        select(CourseBinding).where(CourseBinding.account_id == account.id)
    ).all()
    for b in bindings:
        session.delete(b)
        remaining = session.exec(
            select(CourseBinding).where(CourseBinding.course_id == b.course_id)
        ).first()
        if remaining is None:
            course = session.get(Course, b.course_id)
            if course:
                session.delete(course)

    # 2. 删除签到日志
    logs = session.exec(
        select(CheckInLog).where(CheckInLog.account_id == account.id)
    ).all()
    for l in logs:
        session.delete(l)

    # 3. 删除关联关系
    if user_account is not None:
        session.delete(user_account)
    else:
        user_accounts = session.exec(
            select(UserAccount).where(UserAccount.account_id == account.id)
        ).all()
        for ua in user_accounts:
            session.delete(ua)

    # 4. 删除账号本身
    if force:
        session.delete(account)
    else:
        other_link = session.exec(
            select(UserAccount).where(UserAccount.account_id == account.id)
        ).first()
        if other_link is None:
            session.delete(account)


@router.delete("/{account_id}")
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

        # 强制完整删除（无视是否有其他用户关联）
        _cascade_delete_account(session, account, force=True)
        session.flush()

        return BaseResponse(message="删除成功")
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

        _cascade_delete_account(session, account, user_account)
        session.flush()

        return BaseResponse(message="删除成功")
