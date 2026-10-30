"""kb 模块表模型聚合入口（一表一目录）。

模型已按表拆分到各子模块（``parse_job`` / ``parse_result`` / ``publication`` / ``revision`` /
``chunk`` / ``chat_session`` / ``chat_message`` / ``dup_group``）；这里只做统一导出，
保持 ``from app.modules.kb.models import ...`` 与 Alembic/测试的既有引用不变。

导入本文件即把 8 张表注册进 ``MappedBase.metadata``：迁移基线与测试都依赖这一点，
少了这里，迁移会"看不到表"却没有任何报错。
"""

from .chat_message.model import KbChatMessageModel
from .chat_session.model import KbChatSessionModel
from .chunk.model import KbChunkModel
from .dup_group.model import KbDupGroupModel
from .parse_job.model import KbParseJobModel
from .parse_result.model import KbParseResultModel
from .publication.model import KbPublicationModel
from .revision.model import KbRevisionModel

__all__ = [
    "KbChatMessageModel",
    "KbChatSessionModel",
    "KbChunkModel",
    "KbDupGroupModel",
    "KbParseJobModel",
    "KbParseResultModel",
    "KbPublicationModel",
    "KbRevisionModel",
]
