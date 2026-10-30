"""v1 路由总表：域前缀 → 该域 controller 清单（唯一路由事实来源）。

新增业务模块时：在 app/modules/<域>/<功能>/controller.py 定义 Router，
再到此表登记即可，禁止在 create_app 中散落 include_router。

对外前缀统一为 /api/v1（前端 axios baseURL、Vite 代理键与之一致）；
/docs、/openapi.json、/healthz 等应用级入口仍在根路径，不参与该前缀。
"""

from fastapi import APIRouter

from app.modules.integration_chat.controller import IntegrationChatRouter
from app.modules.integration_chat.session.controller import IntegrationChatSessionRouter
from app.modules.integration_kb.category.controller import IntegrationKbCategoryRouter
from app.modules.integration_kb.chunk.controller import IntegrationKbChunkRouter
from app.modules.integration_kb.document.controller import IntegrationKbDocumentRouter
from app.modules.integration_kb.space.controller import IntegrationKbSpaceRouter
from app.modules.integration_kb.stats.controller import IntegrationKbStatsRouter
from app.modules.integration_kb.task.controller import IntegrationKbTaskRouter
from app.modules.system.access_trace.controller import AccessTraceRouter
from app.modules.system.api_log.controller import SystemApiLogRouter

API_PREFIX = "/api/v1"

DOMAIN_CONTROLLERS: dict[str, list[APIRouter]] = {
    "/system": [
        SystemApiLogRouter,
        AccessTraceRouter,
    ],
    # 集成-知识库：空间 → 分类 → 文档 → 分片 / 任务 / 统计
    "/integration-kb": [
        IntegrationKbSpaceRouter,
        IntegrationKbCategoryRouter,
        IntegrationKbDocumentRouter,
        IntegrationKbChunkRouter,
        IntegrationKbTaskRouter,
        IntegrationKbStatsRouter,
    ],
    # 集成-AI 问答：问答流 + 会话管理
    "/integration-chat": [
        IntegrationChatRouter,
        IntegrationChatSessionRouter,
    ],
}


def _build_router(prefix: str, controllers: list[APIRouter]) -> APIRouter:
    """把同一域下的多个 controller 挂到同一个域前缀下。"""
    router = APIRouter(prefix=prefix)
    for controller in controllers:
        router.include_router(controller)
    return router


api_v1 = APIRouter(prefix=API_PREFIX)
for _prefix, _controllers in DOMAIN_CONTROLLERS.items():
    api_v1.include_router(_build_router(_prefix, _controllers))
