"""启动期表结构与元数据初始化：按模块（域）统一登记与调用，唯一入口 ``ensure_all_tables()``。

约定（所有域一致，新增模块照此办理）：

1. 每个域在自己目录下的 ``bootstrap.py`` 里声明 ``TABLES``（按依赖顺序，被引用表在前），
   并暴露 ``async def ensure_tables() -> list[str]``；导入该文件即把本域模型注册进
   ``MappedBase.metadata``（"元数据"一步）；
2. 本文件只登记与依次调用，不关心某张表的具体归属；任一域失败只告警，不影响其它域；
3. Service 层不再承担建表——DDL 只出现在域的 ``bootstrap.py`` 里；
4. 已存在的表一律跳过（``checkfirst``），不补列、不改类型；当前项目不使用迁移，
   表结构变更需手工调整并随发布评审。

注意：``ensure_all_tables()`` 须在 pgvector 扩展就绪后调用（kb 分片表含向量列与 HNSW 索引）。
"""

from collections.abc import Awaitable, Callable

from app.core.logger import logger
from app.modules.kb.bootstrap import ensure_tables as ensure_kb
from app.modules.system.bootstrap import ensure_tables as ensure_system
from app.modules.voice.bootstrap import ensure_tables as ensure_voice

# 域的调用顺序：kb 域在前（分片表含 pgvector 列，须在 vector 扩展就绪后建；见 app/__init__.py）
MODULE_BOOTSTRAPS: tuple[tuple[str, Callable[[], Awaitable[list[str]]]], ...] = (
    ("kb", ensure_kb),
    ("system", ensure_system),
    ("voice", ensure_voice),
)


async def ensure_all_tables() -> list[str]:
    """依次初始化各域表结构，返回本次新建的表名列表（单域失败只告警）。"""
    created: list[str] = []
    for module, ensure in MODULE_BOOTSTRAPS:
        try:
            created.extend(await ensure())
        except Exception as e:
            logger.error("❌ {} 域表结构初始化失败（该域相关接口将不可用）: {}", module, e)
    return created
