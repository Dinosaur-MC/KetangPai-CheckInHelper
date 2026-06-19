from starlette.exceptions import HTTPException
from fastapi import APIRouter, Depends, Body, Query
from sqlmodel import select

from app.deps import get_current_user
from app.core.db import Session, get_session_with
from app.utils import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate
from app.models import (
    BaseResponse,
    PaginatedResponse,
    User,
    Role,
    UserAccount,
    Course,
    CourseBinding,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/courses", tags=["Course"])

# ================================
#       Course CRUD
# ================================


@router.get("")
async def list_courses(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """列出所有课程（管理员），或用户关联的课程"""
    if current_user.role == Role.admin:
        query = select(Course).order_by(Course.course_name)
        courses, total = paginate(session, query, page, page_size)
    else:
        query = (
            select(Course)
            .join(CourseBinding)
            .join(UserAccount)
            .where(UserAccount.user_id == current_user.id)
            .distinct()
            .order_by(Course.course_name)
        )
        courses, total = paginate(session, query, page, page_size)

    return PaginatedResponse(
        message="success",
        data=[c.model_dump() for c in courses],
        total=total,
        page=page,
        page_size=page_size,
    )


# ================================
#       CourseBinding CRUD
# ================================
# NOTE: binding 路由必须在 /{course_id} 之前注册，否则 FastAPI
#       会将 "bindings" 匹配为 course_id 路径参数。


@router.get("/bindings")
async def list_course_bindings(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session_with),
    page: int = Query(default=DEFAULT_PAGE, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """获取当前用户的所有课程绑定"""
    query = (
        select(CourseBinding)
        .join(UserAccount, CourseBinding.account_id == UserAccount.account_id)
        .where(UserAccount.user_id == current_user.id)
        .order_by(CourseBinding.id)
    )
    bindings, total = paginate(session, query, page, page_size)
    return PaginatedResponse(
        message="success",
        data=[x.model_dump() for x in bindings],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/bindings")
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


@router.delete("/bindings/{binding_id}")
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

    return BaseResponse(message="删除成功")


@router.put("/bindings/{binding_id}")
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


@router.get("/{course_id}")
async def get_course(
    course_id: str,
    session: Session = Depends(get_session_with),
):
    """获取指定课程信息"""
    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    return BaseResponse(message="success", data=course.model_dump())


@router.delete("/{course_id}")
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

    return BaseResponse(message="删除成功")
