"""GSI 用户有效性只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpSysUserModel


class SbpSysUserCRUD(SbpReadCRUD[SbpSysUserModel]):
    """``sbp_sys_user`` 读取。"""

    model = SbpSysUserModel
