"""发布单元子模块：模型 + CRUD + Schema + 服务 + 管理端路由。

合并原 ``knowledge`` 子模块后，本模块统一承载：
- 领域层数据结构（dataclass）；
- 管理端 HTTP 契约（Pydantic Schema）；
- 契约写入服务（``KbEntryService``）；
- 管理端维护服务（``KbPublicationService``）。
"""

from .controller import KbPublicationRouter

__all__ = ["KbPublicationRouter"]
