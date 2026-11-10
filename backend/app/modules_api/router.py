"""`/v1` 契约路由总表（唯一事实来源）。

新增契约接口时：在 ``app/modules_api/kb/<功能>/controller.py`` 定义 Router，
再到此表登记即可；禁止在 ``create_app`` 中散落 ``include_router``（规则 F2）。

协议基座（验签、幂等、nonce、错误体）在 ``app/modules_api/base/``；本表只登记业务域。

路径 / 方法与手册 §五「接口总览」逐条对齐（21 条）：解析 #01–#02、知识 #03–#07 与
#16–#20、会话与生成 #08–#15，以及 v3 新增的 D06 切片查重。
"""

from fastapi import APIRouter

from app.modules_api.kb.chat.controller import ChatRouter
from app.modules_api.kb.duplicate.controller import DuplicateRouter
from app.modules_api.kb.knowledge.controller import KnowledgeRouter
from app.modules_api.kb.parse.controller import ParseRouter

API_PREFIX = "/v1"

DOMAIN_CONTROLLERS: dict[str, list[APIRouter]] = {
    "kb": [
        ParseRouter,
        KnowledgeRouter,
        # 重复治理挂在 /knowledge 前缀下但不属于知识写入口，单独一个 controller 便于按接口族演进
        DuplicateRouter,
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
