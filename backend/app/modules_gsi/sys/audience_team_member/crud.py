"""GSI 受众团队成员只读数据访问（含成员标识联表查询）。"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from app.modules_gsi.base import SbpReadCRUD
from app.modules_gsi.sys.audience_team.model import SbpSysAudienceTeamModel
from app.modules_gsi.sys.user.model import SbpSysUserModel

from .model import SbpSysAudienceTeamMemberModel


class SbpSysAudienceTeamMemberCRUD(SbpReadCRUD[SbpSysAudienceTeamMemberModel]):
    """``sbp_sys_audience_team_member`` 读取。"""

    model = SbpSysAudienceTeamMemberModel

    async def page_with_team(
        self,
        *,
        offset: int,
        limit: int,
        team_id: int | None = None,
        user_id: int | None = None,
        group_code: str | None = None,
        group_name: str | None = None,
        audience_team: str | None = None,
        team_status: str | None = None,
        user_status: str | None = None,
        valid_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """成员 + 团队三标识 + 用户状态联表分页。

        两处 LEFT JOIN 都是必要的：团队或用户被物理清掉时，成员关系行仍要能被看到，
        缺失侧以 ``None`` 返回，页面据此提示「团队/用户不存在」，而不是整行消失。
        """
        team = SbpSysAudienceTeamModel
        user = SbpSysUserModel
        conditions = self._with_team_conditions(
            team_id=team_id,
            user_id=user_id,
            group_code=group_code,
            group_name=group_name,
            audience_team=audience_team,
            team_status=team_status,
            user_status=user_status,
            valid_only=valid_only,
        )
        joins = (
            select(
                self.model.team_id.label("team_id"),
                team.group_code.label("group_code"),
                team.group_name.label("group_name"),
                team.audience_team.label("audience_team"),
                team.status.label("team_status"),
                team.deleted.label("team_deleted"),
                self.model.user_id.label("user_id"),
                user.status.label("user_status"),
                user.allow_login.label("allow_login"),
                user.deleted.label("user_deleted"),
            )
            .select_from(self.model)
            .outerjoin(team, team.id == self.model.team_id)
            .outerjoin(user, user.id == self.model.user_id)
        )
        count_sql = (
            select(func.count())
            .select_from(self.model)
            .outerjoin(team, team.id == self.model.team_id)
            .outerjoin(user, user.id == self.model.user_id)
            .where(*conditions)
        )
        data_sql = joins.where(*conditions).order_by(
            team.id.asc(), self.model.user_id.asc()
        )
        return await self.fetch_rows(
            data_sql=data_sql, count_sql=count_sql, offset=offset, limit=limit
        )

    async def list_teams_by_user(self, user_id: int) -> list[dict[str, Any]]:
        """用户 → 全部团队标识。

        刻意不筛 ``status`` / ``deleted``：反查页要能解释「为什么检索无授权」——
        有团队但被停用/删除，与「一个团队都没有」是两种完全不同的结论。
        """
        team = SbpSysAudienceTeamModel
        sql = (
            select(
                team.id.label("team_id"),
                team.group_code.label("group_code"),
                team.group_name.label("group_name"),
                team.audience_team.label("audience_team"),
                team.status.label("team_status"),
                team.deleted.label("team_deleted"),
            )
            .select_from(self.model)
            .join(team, team.id == self.model.team_id)
            .where(self.model.user_id == user_id)
            .order_by(team.id.asc())
        )
        result = await self.db.execute(sql)
        return [dict(row) for row in result.mappings().all()]

    async def get_user(self, user_id: int) -> SbpSysUserModel | None:
        """按主键取用户有效性（用户不存在返回 ``None``）。"""
        result = await self.db.execute(
            select(SbpSysUserModel).where(SbpSysUserModel.id == user_id).limit(1)
        )
        return result.scalars().first()

    def _with_team_conditions(
        self,
        *,
        team_id: int | None,
        user_id: int | None,
        group_code: str | None,
        group_name: str | None,
        audience_team: str | None,
        team_status: str | None,
        user_status: str | None,
        valid_only: bool,
    ) -> list[ColumnElement]:
        """联表查询条件：团队三标识各自可筛选，空值一律按「不筛选」跳过。"""
        team = SbpSysAudienceTeamModel
        user = SbpSysUserModel
        conditions: list[ColumnElement] = []
        if team_id is not None:
            conditions.append(self.model.team_id == team_id)
        if user_id is not None:
            conditions.append(self.model.user_id == user_id)
        if group_code:
            conditions.append(team.group_code == group_code)
        if group_name:
            conditions.append(team.group_name.like(f"%{group_name}%"))
        if audience_team:
            conditions.append(team.audience_team == audience_team)
        if team_status:
            conditions.append(team.status == team_status)
        if user_status:
            conditions.append(user.status == user_status)
        if valid_only:
            conditions.extend([team.status == "1", team.deleted == 0])
        return conditions
