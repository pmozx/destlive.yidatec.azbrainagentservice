"""重复检测结果子模块：模型 + 数据访问。

刻意不在这里导入 ``service``：``dup_group.model`` 需要复用本包的 ``schema`` 常量，
若本文件再导入 ``service``（其依赖 ``dup_group``），导入期就会形成
``dup_group.model → dup_result → dup_result.service → dup_group`` 的环。
服务层请按需 ``from app.modules.kb.dup_result.service import KbDupResultService``。
"""

from .crud import KbDupResultCRUD
from .model import KbDupResultModel

__all__ = ["KbDupResultCRUD", "KbDupResultModel"]
