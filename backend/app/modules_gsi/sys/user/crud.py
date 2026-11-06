"""GSI 用户档案只读数据访问（含职级联表与关系计数）。"""

from typing import Any

from sqlalchemy import func, select

from app.modules_gsi.base import SbpReadCRUD
from app.modules_gsi.sys.audience_team_member.model import SbpSysAudienceTeamMemberModel
from app.modules_gsi.sys.org_member.model import SbpSysOrgMemberModel
from app.modules_gsi.sys.rank.model import SbpSysRankModel

from .model import SbpSysUserModel


class SbpSysUserCRUD(SbpReadCRUD[SbpSysUserModel]):
    """``sbp_sys_user`` 读取。"""

    model = SbpSysUserModel

    async def page_with_relations(
        self,
        *,
        offset: int,
        limit: int,
        search: dict[str, Any] | None = None,
        org_ids: list[int] | None = None,
        team_id: int | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """用户档案 + 职级名称 + 组织/团队归属数，可按组织范围或团队过滤。

        组织与团队过滤都走 EXISTS 子查询：一个人可挂多个组织、多个团队，
        直接 join 关系表会让同一用户重复成多行，``total`` 变成关系条数、
        分页会把一个人切成两页，语义就错了。

        两个计数用标量子查询，页面一行就能显示「组织 3 / 团队 2」，
        不必为每行再发一轮详情请求。``rank_id`` 允许为空且职级行可能被物理
        清掉，因此职级用 LEFT JOIN：职级缺失只让 ``rank_name`` 为空。
        """
        user = self.model
        rank = SbpSysRankModel
        org_member = SbpSysOrgMemberModel
        team_member = SbpSysAudienceTeamMemberModel
        conditions = self._build_conditions(search or {})
        if org_ids is not None:
            conditions.append(
                select(org_member.user_id)
                .where(org_member.user_id == user.id, org_member.org_id.in_(org_ids))
                .exists()
            )
        if team_id is not None:
            conditions.append(
                select(team_member.user_id)
                .where(
                    team_member.user_id == user.id, team_member.team_id == team_id
                )
                .exists()
            )
        org_count = (
            select(func.count())
            .select_from(org_member)
            .where(org_member.user_id == user.id)
            .scalar_subquery()
        )
        team_count = (
            select(func.count())
            .select_from(team_member)
            .where(team_member.user_id == user.id)
            .scalar_subquery()
        )
        # rank_name 不在模型列上，按它排序会被 _parse_order 跳过，此时回退主键排序
        order_columns = self._parse_order(order_by or []) or [user.id.asc()]
        data_sql = (
            select(
                user.id.label("id"),
                user.username.label("username"),
                user.cn_name.label("cn_name"),
                user.en_name.label("en_name"),
                user.rank_id.label("rank_id"),
                rank.rank_name.label("rank_name"),
                user.position_name.label("position_name"),
                user.status.label("status"),
                user.allow_login.label("allow_login"),
                user.deleted.label("deleted"),
                org_count.label("org_count"),
                team_count.label("team_count"),
            )
            .select_from(user)
            .outerjoin(rank, rank.id == user.rank_id)
            .where(*conditions)
            .order_by(*order_columns)
        )
        count_sql = select(func.count()).select_from(user).where(*conditions)
        return await self.fetch_rows(
            data_sql=data_sql, count_sql=count_sql, offset=offset, limit=limit
        )

    async def get_with_rank(self, user_id: int) -> dict[str, Any] | None:
        """单个用户档案 + 职级名称；用户不存在返回 ``None``。"""
        user = self.model
        rank = SbpSysRankModel
        sql = (
            select(
                user.id.label("id"),
                user.username.label("username"),
                user.cn_name.label("cn_name"),
                user.en_name.label("en_name"),
                user.rank_id.label("rank_id"),
                rank.rank_name.label("rank_name"),
                user.position_name.label("position_name"),
                user.status.label("status"),
                user.allow_login.label("allow_login"),
                user.deleted.label("deleted"),
            )
            .select_from(user)
            .outerjoin(rank, rank.id == user.rank_id)
            .where(user.id == user_id)
            .limit(1)
        )
        result = await self.db.execute(sql)
        row = result.mappings().first()
        return dict(row) if row else None
