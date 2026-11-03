"""kb 模块表模型聚合入口（一表一目录）。

模型已按表拆分到各子模块（``parse_job`` / ``parse_result`` / ``knowledge`` / ``revision`` /
``chunk`` / ``chat_session`` / ``chat_message`` / ``dup_group`` / ``dup_result``）；这里只做统一导出，
保持 ``from app.modules.kb.models import ...`` 与启动期建表/测试的既有引用不变。

导入本文件即把 9 张表注册进 ``MappedBase.metadata``：启动期建表与测试都依赖这一点，
少了这里，建表会“看不到表”却没有任何报错。

注：原 ``entry_meta`` 子模块已合并入 ``publication``（v1.4，2026-11-03），
原 ``knowledge`` 子模块亦已合并入 ``publication``（v1.5，2026-11-03）；
P6 新增 ``dup_result``（重复检测只读结果快照，v1.7，2026-11-03）。
"""

from .chat_message.model import KbChatMessageModel
from .chat_session.model import KbChatSessionModel
from .chunk.model import KbChunkModel
from .dup_group.model import KbDupGroupModel
from .dup_result.model import KbDupResultModel
from .parse_job.model import KbParseJobModel
from .parse_result.model import KbParseResultModel
from .publication.model import KbPublicationModel
from .revision.model import KbRevisionModel

__all__ = [
    "KbChatMessageModel",
    "KbChatSessionModel",
    "KbChunkModel",
    "KbDupGroupModel",
    "KbDupResultModel",
    "KbParseJobModel",
    "KbParseResultModel",
    "KbPublicationModel",
    "KbRevisionModel",
]
