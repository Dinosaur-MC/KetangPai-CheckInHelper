from datetime import datetime, timezone
from typing import Optional
from enum import StrEnum
from sqlmodel import SQLModel, Field

from pydantic import BaseModel


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(default=None, foreign_key="user.id")
    account_id: int = Field(default=None, foreign_key="account.id")


class CourseBinding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: str
    account_id: int = Field(default=None, foreign_key="account.id")
    is_active: bool = True


class CheckInLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(default=None, foreign_key="user.id")
    account_id: int = Field(default=None, foreign_key="account.id")
    course_id: str
    status: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
