from fastapi import APIRouter

from .log.controller import LogRouter
from .user.controller import UserRouter
from .vector.controller import VectorRouter

system_router = APIRouter(prefix="/system")

system_router.include_router(UserRouter)
system_router.include_router(LogRouter)
system_router.include_router(VectorRouter)
