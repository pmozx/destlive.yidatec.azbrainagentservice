"""v1 路由总表：域前缀 → 该域 controller 清单（唯一路由事实来源）。

新增业务模块时：在 app/modules/<域>/<功能>/controller.py 定义 Router，
再到此表登记即可，禁止在 create_app 中散落 include_router。

对外前缀统一为 /api/v1（前端 axios baseURL、Vite 代理键与之一致）；
/docs、/openapi.json、/healthz 等应用级入口仍在根路径，不参与该前缀。
"""

from fastapi import APIRouter

from app.modules_gsi.faq.faq.controller import SbpFaqRouter
from app.modules_gsi.faq.faq_publication.controller import SbpFaqPublicationRouter
from app.modules_gsi.faq.faq_record.controller import SbpFaqRecordRouter
from app.modules_gsi.km.doc.controller import SbpKmDocRouter
from app.modules_gsi.km.doc_publication.controller import SbpKmDocPublicationRouter
from app.modules_gsi.km.doc_record.controller import SbpKmDocRecordRouter
from app.modules_gsi.sys.audience_team.controller import SbpSysAudienceTeamRouter
from app.modules_gsi.sys.audience_team_member.controller import SbpSysAudienceTeamMemberRouter
from app.modules_gsi.sys.org.controller import SbpSysOrgRouter
from app.modules_gsi.sys.org_member.controller import SbpSysOrgMemberRouter
from app.modules_gsi.sys.rank.controller import SbpSysRankRouter
from app.modules_gsi.sys.user.controller import SbpSysUserRouter
from app.modules.kb.chat.controller import KbChatRouter
from app.modules.kb.chat_message.controller import KbChatMessageRouter
from app.modules.kb.chat_metric.controller import KbChatTurnMetricRouter
from app.modules.kb.chat_session.controller import KbChatSessionRouter
from app.modules.kb.dup_group.controller import KbDupGroupRouter
from app.modules.kb.publication.controller import KbPublicationRouter
from app.modules.kb.parse_job.admin_controller import KbParseJobRouter
from app.modules.kb.retrieval.controller import KbRetrievalRouter
from app.modules.system.access_trace.controller import AccessTraceRouter
from app.modules.system.api_log.controller import SystemApiLogRouter
from app.modules.system.model_config.controller import SystemModelConfigRouter
from app.modules.system.storage.controller import StorageRouter

API_PREFIX = "/api/v1"

DOMAIN_CONTROLLERS: dict[str, list[APIRouter]] = {
    "/system": [
        SystemApiLogRouter,
        AccessTraceRouter,
        StorageRouter,
        SystemModelConfigRouter,
    ],
    "/kb": [
        KbPublicationRouter,
        KbParseJobRouter,
        KbDupGroupRouter,
        KbRetrievalRouter,
        KbChatSessionRouter,
        KbChatMessageRouter,
        KbChatTurnMetricRouter,
        KbChatRouter,
    ],
    "/gsi": [
        SbpSysOrgRouter,
        SbpSysOrgMemberRouter,
        SbpSysAudienceTeamRouter,
        SbpSysAudienceTeamMemberRouter,
        SbpSysUserRouter,
        SbpSysRankRouter,
        SbpKmDocRouter,
        SbpKmDocPublicationRouter,
        SbpKmDocRecordRouter,
        SbpFaqRouter,
        SbpFaqPublicationRouter,
        SbpFaqRecordRouter,
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
