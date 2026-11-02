"""`/v1` 契约路由总表（唯一事实来源）。

新增契约接口时：在 ``app/modules_api/contract/<功能>/controller.py`` 定义 Router，
再到此表登记即可；禁止在 ``create_app`` 中散落 ``include_router``（规则 F2）。

路径 / 方法与手册 §五「接口总览」逐条对齐（20 条）：解析 #01–#02、知识 #03–#07 与
#16–#20、会话与生成 #08–#15。
"""

from fastapi import APIRouter

from app.modules_api.contract.chat.controller import ChatRouter
from app.modules_api.contract.knowledge.controller import KnowledgeRouter
from app.modules_api.contract.parse.controller import ParseRouter

API_PREFIX = "/v1"

DOMAIN_CONTROLLERS: dict[str, list[APIRouter]] = {
    "contract": [
        ParseRouter,
        KnowledgeRouter,
        ChatRouter,
    ],
}


def _build_router(controllers: list[APIRouter]) -> APIRouter:
    """把同一域下的多个 controller 收口到一个总表 Router。"""
    router = APIRouter()
    for controller in controllers:
        router.include_router(controller)
    return router


api_router = APIRouter(prefix=API_PREFIX)
for _controllers in DOMAIN_CONTROLLERS.values():
    api_router.include_router(_build_router(_controllers))
