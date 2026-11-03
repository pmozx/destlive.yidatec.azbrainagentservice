"""知识平台域：知识解析、写入与版本、检索、会话问答、重复治理，表前缀 ``sbp_ai_kb_``。

已按「一表一目录」完成分层：``parse_job`` / ``parse_result`` / ``publication`` / ``revision`` /
``chunk`` / ``chat_session`` / ``chat_message`` / ``dup_group`` 各自带 ``model.py`` + ``crud.py``；
业务 Service / Controller 自 T2.x 起按接口逐个补齐。``models.py`` 只做聚合导出，
导入它即注册到元数据，勿删。

注：原 ``entry_meta`` 子模块已合并入 ``publication``（v1.4，2026-11-03），
原 ``knowledge`` 子模块亦已合并入 ``publication``（v1.5，2026-11-03）。
"""

from . import models  # noqa: F401  # 导入即注册（勿删）
