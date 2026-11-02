"""GSI 知识生效快照只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpKmDocRecordModel


class SbpKmDocRecordCRUD(SbpReadCRUD[SbpKmDocRecordModel]):
    """``sbp_km_doc_record`` 读取。"""

    model = SbpKmDocRecordModel
