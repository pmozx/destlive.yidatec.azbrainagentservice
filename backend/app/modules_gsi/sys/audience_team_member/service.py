"""GSI 受众团队成员只读服务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.modules_gsi.sys.user.crud import SbpSysUserCRUD
from app.utils.common_util import search_to_dict

from .crud import SbpSysAudienceTeamMemberCRUD
from .schema import (
    SbpSysAudienceTeamMemberOutSchema,
    SbpSysAudienceTeamMemberQueryParam,
    SbpSysAudienceTeamMemberWithTeamOutSchema,
    SbpSysAudienceTeamMemberWithTeamQueryParam,
    SbpUserTeamOutSchema,
    SbpUserTeamsOutSchema,
)


class SbpSysAudienceTeamMemberService:
    """``sbp_sys_audience_team_member`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysAudienceTeamMemberCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysAudienceTeamMemberQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysAudienceTeamMemberOutSchema]:
        """分页查询受众团队成员。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"team_id": "asc"}, {"user_id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysAudienceTeamMemberOutSchema,
        )

    async def page_with_team(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysAudienceTeamMemberWithTeamQueryParam | None = None,
    ) -> PageResultSchema[SbpSysAudienceTeamMemberWithTeamOutSchema]:
        """成员标识页：成员 + 团队三标识 + 用户有效性。

        成员关联表只有两个数字，授权链又是「user_id → team → group_code → publication.team」，
        所以这里把团队编码/名称/兼容标识与用户状态一次带出，页面上不做二次请求。
        """
        params = search or SbpSysAudienceTeamMemberWithTeamQueryParam()
        offset = (page_no - 1) * page_size
        rows, total = await self.crud.page_with_team(
            offset=offset,
            limit=page_size,
            team_id=params.team_id,
            user_id=params.user_id,
            username=params.username,
            cn_name=params.cn_name,
            group_code=params.group_code,
            group_name=params.group_name,
            audience_team=params.audience_team,
            team_status=params.team_status,
            user_status=params.user_status,
            valid_only=params.valid_only,
        )
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=[
                SbpSysAudienceTeamMemberWithTeamOutSchema.model_validate(row)
                for row in rows
            ],
        )

    async def user_teams(self, user_id: int) -> SbpUserTeamsOutSchema:
        """按用户反查受众团队标识（契约 §四 userTeams 的只读直译）。

        返回全部团队并逐项标注有效性：空集合与「有团队但全部停用」必须能区分，
        否则排障时无法判断该用户是真没授权还是授权被停用掉了。
        """
        rows = await self.crud.list_teams_by_user(user_id)
        teams = [SbpUserTeamOutSchema.model_validate(row) for row in rows]
        user = await SbpSysUserCRUD(self.crud.db).get_with_rank(user_id)
        return SbpUserTeamsOutSchema(
            user_id=user_id,
            user_exists=user is not None,
            user_username=user["username"] if user else None,
            user_cn_name=user["cn_name"] if user else None,
            user_position_name=user["position_name"] if user else None,
            user_rank_id=user["rank_id"] if user else None,
            user_rank_name=user["rank_name"] if user else None,
            user_status=user["status"] if user else None,
            allow_login=user["allow_login"] if user else None,
            user_deleted=user["deleted"] if user else None,
            team_count=len(teams),
            valid_team_count=sum(1 for team in teams if team.team_valid),
            teams=teams,
        )
