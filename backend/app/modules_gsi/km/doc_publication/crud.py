"""GSI 文档按团队生效关系只读数据访问。"""

from sqlalchemy import and_, select

from app.modules_gsi.base import SbpReadCRUD
from app.modules_gsi.sys.audience_team.model import SbpSysAudienceTeamModel
from app.modules_gsi.sys.audience_team_member.model import SbpSysAudienceTeamMemberModel

from .model import SbpKmDocPublicationModel


class SbpKmDocPublicationCRUD(SbpReadCRUD[SbpKmDocPublicationModel]):
    """``sbp_km_doc_publication`` 读取。"""

    model = SbpKmDocPublicationModel

    async def list_authorized_publication_ids(self, *, user_id: int) -> set[str]:
        """按用户受众团队算出可检索的发布单元集合（#07 权限下推的输入）。

        链路（《03-服务大脑GSI账号组织关系与知识权限治理说明》§1.5）：
        ``用户 → audience_team_member.team_id → audience_team.group_code → doc_publication.team``。

        - 授权匹配只认 ``group_code``：``audience_team`` 列仅兼容历史标识，不参与判定；
        - 停用（``status<>'1'``）与逻辑删除（``deleted<>0``）的团队不产生权限；
        - ``index_status`` 只是同步状态，不能单独作为授权依据，这里不据此过滤。
        """
        sql = (
            select(SbpKmDocPublicationModel.id)
            .join(
                SbpSysAudienceTeamModel,
                and_(
                    SbpSysAudienceTeamModel.group_code == SbpKmDocPublicationModel.team,
                    SbpSysAudienceTeamModel.status == "1",
                    SbpSysAudienceTeamModel.deleted == 0,
                ),
            )
            .join(
                SbpSysAudienceTeamMemberModel,
                SbpSysAudienceTeamMemberModel.team_id == SbpSysAudienceTeamModel.id,
            )
            .where(SbpSysAudienceTeamMemberModel.user_id == user_id)
            .distinct()
        )
        result = await self.db.execute(sql)
        return {row[0] for row in result.all() if row[0]}
