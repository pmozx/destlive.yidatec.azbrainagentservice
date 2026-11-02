"""GSI 受众团队只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpSysAudienceTeamModel


class SbpSysAudienceTeamCRUD(SbpReadCRUD[SbpSysAudienceTeamModel]):
    """``sbp_sys_audience_team`` 读取。"""

    model = SbpSysAudienceTeamModel
