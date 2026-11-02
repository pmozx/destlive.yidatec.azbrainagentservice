"""GSI 业务 · ``km/doc_record``。"""

from .crud import SbpKmDocRecordCRUD
from .model import SbpKmDocRecordModel
from .schema import (
    SbpKmDocRecordDetailOutSchema,
    SbpKmDocRecordOutSchema,
    SbpKmDocRecordQueryParam,
)
from .service import SbpKmDocRecordService

__all__ = [
    "SbpKmDocRecordCRUD",
    "SbpKmDocRecordDetailOutSchema",
    "SbpKmDocRecordModel",
    "SbpKmDocRecordOutSchema",
    "SbpKmDocRecordQueryParam",
    "SbpKmDocRecordService",
]
