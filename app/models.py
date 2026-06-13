import secrets
from datetime import datetime, timezone
from typing import Optional
from enum import StrEnum
from sqlmodel import SQLModel, Field

from pydantic import BaseModel


def generate_invite_code() -> str:
    """生成 16 位大写字母数字混合的邀请码。"""
    return secrets.token_hex(12).upper()[:16]


class BaseResponse(BaseModel):
    code: int = 200
    message: str
    data: Optional[dict | list] = None


class ErrorResponse(BaseModel):
    code: int
    message: str
    detail: Optional[dict | list] = None


class Role(StrEnum):
    admin = "admin"
    user = "user"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password: str
    role: str = Role.user
    is_active: bool = True
    last_login_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password: str
    uid: str
    status: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(default=None, foreign_key="user.id", index=True)
    account_id: int = Field(default=None, foreign_key="account.id", index=True)


class Course(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    code: str
    course_name: str
    semester: str
    term: str


class CourseBinding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: str = Field(default=None, foreign_key="course.id", index=True)
    account_id: int = Field(default=None, foreign_key="account.id", index=True)
    is_active: bool = True


class CheckInLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(default=None, foreign_key="user.id")
    account_id: int = Field(default=None, foreign_key="account.id")
    course_id: str
    status: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InviteCode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(default_factory=generate_invite_code, index=True, unique=True)
    is_active: bool = True
    max_uses: Optional[int] = None
    used_count: int = 0
    expires_at: Optional[datetime] = None
    created_by: int = Field(default=None, foreign_key="user.id")
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SystemSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""
