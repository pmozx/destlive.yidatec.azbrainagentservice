"""GSI 业务模块 ``faq``：FAQ 主记录、按团队生效关系、审批快照。

FAQ 与知识侧（``km``）的治理语义不一致，不能照抄：

- 审批阶段是 ``version / owner / audience / owner_final`` 四阶段；
- 黑名单与有效期随审批**快照化**到 ``access_rules_json`` / ``snapshot_json``，
  而知识侧的黑名单是编辑态即时生效；
- 授权规则优先读 ``publication.access_rules_json``，为空才回落 ``record.snapshot_json``，
  两者都必须含 ``invisibleTeams`` 数组，缺失即拒绝。
"""

from .faq import (
    SbpFaqCRUD,
    SbpFaqModel,
    SbpFaqOutSchema,
    SbpFaqQueryParam,
    SbpFaqService,
)
from .faq_publication import (
    SbpFaqPublicationCRUD,
    SbpFaqPublicationModel,
    SbpFaqPublicationOutSchema,
    SbpFaqPublicationQueryParam,
    SbpFaqPublicationService,
    resolve_access_rules,
)
from .faq_record import (
    SbpFaqRecordCRUD,
    SbpFaqRecordDetailOutSchema,
    SbpFaqRecordModel,
    SbpFaqRecordOutSchema,
    SbpFaqRecordQueryParam,
    SbpFaqRecordService,
)

__all__ = [
    "SbpFaqCRUD",
    "SbpFaqModel",
    "SbpFaqOutSchema",
    "SbpFaqPublicationCRUD",
    "SbpFaqPublicationModel",
    "SbpFaqPublicationOutSchema",
    "SbpFaqPublicationQueryParam",
    "SbpFaqPublicationService",
    "SbpFaqQueryParam",
    "SbpFaqRecordCRUD",
    "SbpFaqRecordDetailOutSchema",
    "SbpFaqRecordModel",
    "SbpFaqRecordOutSchema",
    "SbpFaqRecordQueryParam",
    "SbpFaqRecordService",
    "SbpFaqService",
    "resolve_access_rules",
]
