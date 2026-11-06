"""GSI 职级只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpSysRankModel


class SbpSysRankCRUD(SbpReadCRUD[SbpSysRankModel]):
    """``sbp_sys_rank`` 读取。"""

    model = SbpSysRankModel
