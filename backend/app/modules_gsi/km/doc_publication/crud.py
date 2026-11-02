"""GSI 文档按团队生效关系只读数据访问。"""

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpKmDocPublicationModel


class SbpKmDocPublicationCRUD(SbpReadCRUD[SbpKmDocPublicationModel]):
    """``sbp_km_doc_publication`` 读取。"""

    model = SbpKmDocPublicationModel
