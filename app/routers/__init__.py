from .account import router as account_router
from .auth import router as auth_router
from .checkin import router as checkin_router
from .course import router as course_router
from .invite_code import router as invite_code_router
from .log import router as log_router
from .settings import router as settings_router
from .user import router as user_router

__all__ = [
    "account_router",
    "auth_router",
    "checkin_router",
    "course_router",
    "invite_code_router",
    "log_router",
    "settings_router",
    "user_router",
]
