"""GSI FAQ 按团队生效关系只读服务。"""

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.modules_gsi.base import format_gsi_datetime

from .crud import SbpFaqPublicationCRUD
from .schema import SbpFaqPublicationOutSchema, SbpFaqPublicationQueryParam


def resolve_access_rules(
    access_rules_json: str | None, snapshot_json: str | None
) -> dict[str, Any]:
    """解析生效授权规则，返回来源 / 有效性 / 黑名单 / 失败原因。

    口径（契约 §四）：``publication.access_rules_json`` 优先，为空才回落
    ``record.snapshot_json``；规则必须是 JSON 对象且含 ``invisibleTeams`` **数组**，
    缺失或解析失败一律判无效——不能拿编辑态受众表补权限。
    """
    publication_raw = (access_rules_json or "").strip()
    record_raw = (snapshot_json or "").strip()
    source = "publication" if publication_raw else ("record" if record_raw else "none")
    raw = publication_raw or record_raw
    if not raw:
        return {
            "rules_source": source,
            "rules_valid": False,
            "invisible_teams": [],
            "rules_error": "生效规则为空：该发布单元不可用",
        }
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {
            "rules_source": source,
            "rules_valid": False,
            "invisible_teams": [],
            "rules_error": f"规则 JSON 解析失败：{exc}",
        }
    if not isinstance(payload, dict):
        return {
            "rules_source": source,
            "rules_valid": False,
            "invisible_teams": [],
            "rules_error": "规则不是 JSON 对象",
        }
    teams = payload.get("invisibleTeams")
    if not isinstance(teams, list):
        return {
            "rules_source": source,
            "rules_valid": False,
            "invisible_teams": [],
            "rules_error": "规则缺少 invisibleTeams 数组",
        }
    return {
        "rules_source": source,
        "rules_valid": True,
        "invisible_teams": [str(team).strip() for team in teams if str(team).strip()],
        "rules_error": None,
    }


class SbpFaqPublicationService:
    """``sbp_faq_publication`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpFaqPublicationCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpFaqPublicationQueryParam | None = None,
    ) -> PageResultSchema[SbpFaqPublicationOutSchema]:
        """分页查询 FAQ 生效关系，并逐行解析生效规则。"""
        params = search or SbpFaqPublicationQueryParam()
        offset = (page_no - 1) * page_size
        rows, total = await self.crud.page_with_rules(
            offset=offset,
            limit=page_size,
            faq_id=params.faq_id,
            team=params.team,
            record_id=params.record_id,
            index_status=params.index_status,
        )
        items = []
        for row in rows:
            rules = resolve_access_rules(
                row.get("access_rules_json"), row.get("snapshot_json")
            )
            items.append(
                SbpFaqPublicationOutSchema.model_validate(
                    {
                        "id": row["id"],
                        "faq_id": row["faq_id"],
                        "team": row["team"],
                        "record_id": row["record_id"],
                        "index_status": row["index_status"],
                        # GSI 时间列落库即业务本地时间，走原样格式化，不能再做时区换算
                        "updated_at": format_gsi_datetime(row.get("updated_at")),
                        "access_rules_json": row.get("access_rules_json"),
                        **rules,
                    }
                )
            )
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=items,
        )
