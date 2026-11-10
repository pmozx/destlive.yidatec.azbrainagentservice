"""GSI 文档按团队生效关系只读服务。"""

import json
from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.logger import logger
from app.modules_gsi.base import format_gsi_datetime
from app.utils.common_util import search_to_dict
from app.utils.time_util import app_timezone

from .crud import SbpKmDocPublicationCRUD
from .schema import (
    EffectiveRule,
    SbpKmDocPublicationBrief,
    SbpKmDocPublicationOutSchema,
    SbpKmDocPublicationQueryParam,
    SbpKmDocPublicationWithTeamOutSchema,
    SbpKmDocPublicationWithTeamQueryParam,
)


def resolve_effective_rule(snapshot_json: str | None) -> EffectiveRule:
    """解析生效快照规则（契约 §四 第 4 步）。

    口径：``invisibleTeams`` 必须是数组，缺失 / 不是对象 / 解析失败一律判无效——
    拿不到黑名单就无法证明没被排除，默认为空等于把拉黑变成放行。
    ``expireAt`` 为空表示长期有效；格式认不出也判无效（无法判定是否已过期）。
    """
    raw = (snapshot_json or "").strip()
    if not raw:
        return EffectiveRule(valid=False, reason="生效快照为空")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return EffectiveRule(valid=False, reason=f"快照 JSON 解析失败：{exc}")
    if not isinstance(payload, dict):
        return EffectiveRule(valid=False, reason="快照不是 JSON 对象")
    teams = payload.get("invisibleTeams")
    if not isinstance(teams, list):
        return EffectiveRule(valid=False, reason="快照缺少 invisibleTeams 数组")
    expire_at, expire_error = _parse_expire_at(payload.get("expireAt"))
    if expire_error is not None:
        return EffectiveRule(valid=False, reason=expire_error)
    return EffectiveRule(
        valid=True,
        invisible_teams=tuple(str(team).strip() for team in teams if str(team).strip()),
        expire_at=expire_at,
    )


def _parse_expire_at(value: Any) -> tuple[date | None, str | None]:
    """``expireAt`` → 到期日；空值返回 ``(None, None)``，格式非法返回原因。

    只取前 10 位日期部分：快照里既有 ``YYYY-MM-DD`` 也有带时区的 ISO 8601，
    而契约要的是「早于当天就拒绝」，比较粒度就是业务日期，不做时区换算。
    """
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "expireAt 不是字符串"
    text = value.strip()
    if not text:
        return None, None
    try:
        return date.fromisoformat(text[:10]), None
    except ValueError:
        return None, f"expireAt 格式无法识别：{text}"


def is_rule_visible(
    rule: EffectiveRule,
    *,
    user_teams: Collection[str],
    today: date,
) -> bool:
    """规则 + 用户团队 → 是否可见（排除优先：黑名单命中即拒绝，再看有效期）。"""
    if not rule.valid:
        return False
    if set(rule.invisible_teams) & set(user_teams):
        return False
    return rule.expire_at is None or rule.expire_at >= today


class SbpKmDocPublicationService:
    """``sbp_km_doc_publication`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpKmDocPublicationCRUD(db)

    async def list_authorized_publication_ids(self, user_id: str) -> set[str]:
        """签名 ``user_id`` → 可检索发布单元集合（#07 检索授权，T4.1）。

        契约把 ``user_id`` 定义成「稳定数字主键的字符串形式」。脏数据或伪造值不做报错、
        也不放行：转不成主键就返回空集——「什么都看不到」是唯一安全的结果；
        GSI 表不可用时异常直接上抛，由契约层转 503 ``AUTHORIZATION_UNAVAILABLE``
        （禁止降级成全库检索）。

        三步（对应契约 §四「候选数据必须继续执行的判断」2–4）：

        1. 取用户仍有效的团队编码；空集立即返回空集，绝不表示全库；
        2. 按团队取候选发布单元，SQL 侧已拒绝逻辑删除 / 已归档 / 已下架的**主记录**；
        3. 逐条解析生效快照：``invisibleTeams`` 命中用户任一团队即拒绝，
           ``expireAt`` 早于当天（业务时区）即拒绝；规则不可解析也拒绝。

        第 3 步只能放应用层：``snapshot_json`` 在 GSI 侧是 ``text``，无法用 ``->>`` 下推。
        """
        try:
            pk = int(str(user_id).strip())
        except (TypeError, ValueError):
            logger.warning("检索授权：user_id 不是数字主键，按无权限处理: {!r}", user_id)
            return set()

        team_keys = await self.crud.list_member_team_keys(user_id=pk)
        if not team_keys:
            return set()

        today = datetime.now(app_timezone()).date()
        visible: set[str] = set()
        rejected_rules = 0
        for candidate in await self.crud.list_authorized_candidates(team_keys=sorted(team_keys)):
            rule = resolve_effective_rule(candidate.snapshot_json)
            if is_rule_visible(rule, user_teams=team_keys, today=today):
                visible.add(candidate.publication_id)
            elif not rule.valid:
                rejected_rules += 1
        if rejected_rules:
            # 不把不可解析的规则当「没配置」放行；这里留痕，便于发现 GSI 快照格式变化
            logger.warning(
                "检索授权：{} 个发布单元的生效快照规则不可解析，已按不可见处理（user_id={}）",
                rejected_rules,
                user_id,
            )
        return visible

    async def map_teams(self, publication_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """发布单元 → 受众团队名称（跨受众合并判断的输入）。

        重复治理把「多个团队的发布单元合成一条知识」时，必须能看出受众是否重叠：
        这里只负责把团队名查出来，是否算扩大了可见范围由契约层判定。
        """
        teams = await self.crud.map_team_names(publication_ids)
        return {publication_id: tuple(names) for publication_id, names in teams.items()}

    async def map_briefs(self, publication_ids: Sequence[str]) -> dict[str, SbpKmDocPublicationBrief]:
        """发布单元 → 业务展示信息（标题、团队、同步状态）。

        两条查询而不是 JOIN 一次出：一个发布单元可能对应多个团队，JOIN 会让行数膨胀，
        调用方还得自己去重；分开查再合并语义更直接。
        """
        rows = await self.crud.list_by_ids(publication_ids)
        teams = await self.map_teams([row.id for row in rows])
        return {
            row.id: SbpKmDocPublicationBrief(
                publication_id=row.id,
                doc_id=row.doc_id,
                record_id=row.record_id,
                index_status=row.index_status,
                teams=teams.get(row.id, ()),
            )
            for row in rows
        }

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpKmDocPublicationQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpKmDocPublicationOutSchema]:
        """分页查询文档按团队生效关系。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"updated_at": "desc"}],
            search=search_to_dict(search),
            out_schema=SbpKmDocPublicationOutSchema,
        )

    async def page_with_team(
        self,
        page_no: int,
        page_size: int,
        search: SbpKmDocPublicationWithTeamQueryParam | None = None,
    ) -> PageResultSchema[SbpKmDocPublicationWithTeamOutSchema]:
        """生效关系页：把 ``team`` 解析成「编码 + 名称 + 兼容标识」。"""
        params = search or SbpKmDocPublicationWithTeamQueryParam()
        offset = (page_no - 1) * page_size
        rows, total = await self.crud.page_with_team(
            offset=offset,
            limit=page_size,
            doc_id=params.doc_id,
            team=params.team,
            record_id=params.record_id,
            index_status=params.index_status,
            team_name=params.team_name,
        )
        items = []
        for row in rows:
            # GSI 时间列落库即业务本地时间，走原样格式化，不能再做时区换算
            row["updated_at"] = format_gsi_datetime(row.get("updated_at"))
            items.append(SbpKmDocPublicationWithTeamOutSchema.model_validate(row))
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=items,
        )
