"""Modular APIRouters for Life-Inbox."""

from app.routers.auth import router as auth_router
from app.routers.calendar import router as calendar_router
from app.routers.intelligence import router as intelligence_router
from app.routers.items import router as items_router
from app.routers.me import router as me_router
from app.routers.system import router as system_router
from app.routers.tasks import router as tasks_router

__all__ = [
    "auth_router",
    "calendar_router",
    "intelligence_router",
    "items_router",
    "me_router",
    "system_router",
    "tasks_router",
]
