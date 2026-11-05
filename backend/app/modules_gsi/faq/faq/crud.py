"""GSI FAQ 主记录只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpFaqModel


class SbpFaqCRUD(SbpReadCRUD[SbpFaqModel]):
    """``sbp_faq`` 读取。"""

    model = SbpFaqModel
