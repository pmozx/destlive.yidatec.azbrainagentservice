"""GSI 业务 · ``faq/faq_record``。"""

from .crud import SbpFaqRecordCRUD
from .model import SbpFaqRecordModel
from .schema import (
    SbpFaqRecordDetailOutSchema,
    SbpFaqRecordOutSchema,
    SbpFaqRecordQueryParam,
)
from .service import SbpFaqRecordService

__all__ = [
    "SbpFaqRecordCRUD",
    "SbpFaqRecordDetailOutSchema",
    "SbpFaqRecordModel",
    "SbpFaqRecordOutSchema",
    "SbpFaqRecordQueryParam",
    "SbpFaqRecordService",
]
