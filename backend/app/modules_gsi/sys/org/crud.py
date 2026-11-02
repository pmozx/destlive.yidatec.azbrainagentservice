"""GSI 组织架构只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpSysOrgModel


class SbpSysOrgCRUD(SbpReadCRUD[SbpSysOrgModel]):
    """``sbp_sys_org`` 读取。"""

    model = SbpSysOrgModel
