"""知识平台域：知识解析、写入与版本、检索、会话问答、重复治理，表前缀 ``sbp_ai_kb_``。

P1（T1.0）只建表模型；业务实现自 T2.x 起按接口逐个补齐（届时按「一表一目录」拆出
``crud.py`` / ``service.py`` / ``controller.py``）。导入 ``models`` 即注册到元数据，勿删。
"""

from . import models  # noqa: F401  # 导入即注册（勿删）
