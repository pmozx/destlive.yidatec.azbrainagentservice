"""集成-知识库-文档模型（表 sbp_ai_integration_kb_document）。

文档状态机（一次解析任务内完成「解析 → 分片 → 向量化 → 入库」，因此不区分解析/向量化两段）：

- 0 PENDING 待解析：刚上传，或上次解析被中断（启动时 PARSING 会被重置回 PENDING）
- 1 PARSING 解析中：已提交到进程内解析任务
- 2 DONE    已完成：分片与向量已入库，可被检索
- 3 FAILED  失败：error_msg 记录原因，可重新提交解析
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

INTEGRATION_KB_DOCUMENT_TABLE_NAME: str = "sbp_ai_integration_kb_document"


class DocumentStatus(IntEnum):
    """文档解析状态（前端标签与服务端可重试判断的唯一依据）。"""

    PENDING = 0
    PARSING = 1
    DONE = 2
    FAILED = 3


class PipelineStatus(IntEnum):
    """文档流水线状态（对外口径）。

    由该文档**最新一条任务**的状态推导（文档表不冗余存一份，避免两处口径漂移）：
    待执行 / 执行中 / 已完成 / 失败，与 ``TaskStatus`` 一一对应（取消按失败回显）。
    """

    PENDING = 0
    RUNNING = 1
    DONE = 2
    FAILED = 3


class IntegrationKbDocumentModel(ModelMixin):
    """知识文档表（原件入对象存储，解析产物与分片分别落对象存储与向量表）"""

    __tablename__: str = INTEGRATION_KB_DOCUMENT_TABLE_NAME
    __table_args__: tuple = (
        Index("ix_sbp_ai_integration_kb_document_space_status", "space_id", "status"),
        Index("ix_sbp_ai_integration_kb_document_space_md5", "space_id", "file_md5"),
        Index("ix_sbp_ai_integration_kb_document_category", "category_id"),
        {"comment": "集成-知识库-文档表"},
    )

    space_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="所属空间ID")
    category_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="所属分类ID(未分类为空)")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="文档标题")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    file_type: Mapped[str] = mapped_column(String(32), default="", nullable=False, comment="文件类型(小写后缀，不含点)")
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, comment="文件大小(字节)")
    file_md5: Mapped[str] = mapped_column(String(32), default="", nullable=False, comment="文件MD5(空间内去重依据)")
    object_key: Mapped[str] = mapped_column(String(512), default="", nullable=False, comment="原件对象键")
    keywords: Mapped[str] = mapped_column(String(255), default="", nullable=False, comment="关键词(逗号分隔)")
    source: Mapped[str] = mapped_column(String(64), default="", nullable=False, comment="来源")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="版本号")
    status: Mapped[int] = mapped_column(
        Integer, default=int(DocumentStatus.PENDING), nullable=False, index=True, comment="状态(0:待解析 1:解析中 2:已完成 3:失败)"
    )
    parse_progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="解析进度(0-100)")
    error_msg: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="失败原因")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="分片条数")
    ref_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="被问答引用次数")
    parsed_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="解析正文字符数")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="最近一次解析耗时(毫秒)")
    preview: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="解析正文预览(人工核验)")
    parsed_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="最近一次解析完成时间")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否上架参与检索")
