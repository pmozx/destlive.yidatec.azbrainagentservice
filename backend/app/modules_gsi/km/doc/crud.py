"""GSI 知识文档元数据只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpKmDocModel


class SbpKmDocCRUD(SbpReadCRUD[SbpKmDocModel]):
    """``sbp_km_doc`` 读取。"""

    model = SbpKmDocModel
