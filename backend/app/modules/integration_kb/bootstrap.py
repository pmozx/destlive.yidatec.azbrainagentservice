"""集成-知识库启动引导：建表与解析器安装。

为什么不用 Alembic：本项目表结构随集成迭代频繁调整，启动期幂等建表（``checkfirst``）
足够支撑当前阶段；定稿后再补迁移脚本。分片表由 ``ensure_vector_store`` 负责
（它还要建 vector 扩展与 HNSW 索引，非 PostgreSQL 环境会明确报错而不是静默跳过）。
"""

from app.core.database import async_engine
from app.core.logger import logger

from .category.model import IntegrationKbCategoryModel
from .document.model import IntegrationKbDocumentModel
from .space.model import IntegrationKbSpaceModel
from .task.model import IntegrationKbTaskModel, IntegrationKbTaskStepModel

# 建表顺序即依赖顺序：任务步骤引用任务、任务台账引用文档、分类引用空间
KB_TABLES = (
    IntegrationKbSpaceModel.__table__,
    IntegrationKbCategoryModel.__table__,
    IntegrationKbDocumentModel.__table__,
    IntegrationKbTaskModel.__table__,
    IntegrationKbTaskStepModel.__table__,
)


async def ensure_kb_tables() -> None:
    """幂等创建知识库五张业务表（空间 / 分类 / 文档 / 任务 / 任务步骤）。"""
    async with async_engine.begin() as conn:
        for table in KB_TABLES:
            await conn.run_sync(table.create, checkfirst=True)
    logger.info("==== 知识库表就绪（{} 张）====", len(KB_TABLES))


async def install_parser() -> None:
    """安装 MinerU 解析器（PDF/Office/图片的内容提取主路径）。

    ``MINERU_ENABLE=false`` 时只用内置纯文本/HTML 解析器，二进制文档会明确报不支持。
    """
    from .pipeline.mineru_parser import install_mineru_parser

    install_mineru_parser()
