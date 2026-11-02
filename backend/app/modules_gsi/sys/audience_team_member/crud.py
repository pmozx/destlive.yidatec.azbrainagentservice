"""GSI 受众团队成员只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpSysAudienceTeamMemberModel


class SbpSysAudienceTeamMemberCRUD(SbpReadCRUD[SbpSysAudienceTeamMemberModel]):
    """``sbp_sys_audience_team_member`` 读取。"""

    model = SbpSysAudienceTeamMemberModel
