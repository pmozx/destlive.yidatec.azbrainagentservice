"""GSI 组织成员只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpSysOrgMemberModel


class SbpSysOrgMemberCRUD(SbpReadCRUD[SbpSysOrgMemberModel]):
    """``sbp_sys_org_member`` 读取。"""

    model = SbpSysOrgMemberModel
