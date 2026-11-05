"""GSI FAQ 审批快照只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpFaqRecordModel


class SbpFaqRecordCRUD(SbpReadCRUD[SbpFaqRecordModel]):
    """``sbp_faq_record`` 读取。"""

    model = SbpFaqRecordModel
