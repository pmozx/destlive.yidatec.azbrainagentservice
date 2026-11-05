"""GSI 组织架构只读服务。"""

from collections import defaultdict
from collections.abc import Sequence
from typing import cast

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import SbpSysOrgCRUD
from .model import SbpSysOrgModel
from .schema import SbpSysOrgOutSchema, SbpSysOrgQueryParam, SbpSysOrgTreeNodeSchema


class SbpSysOrgService:
    """``sbp_sys_org`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysOrgCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysOrgQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysOrgOutSchema]:
        """分页查询组织架构。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysOrgOutSchema,
        )

    async def detail(self, id: int) -> SbpSysOrgOutSchema:
        """查询组织详情。"""
        return cast(
            SbpSysOrgOutSchema,
            await self.crud.get_or_404(
                id=id, msg="GSI 组织不存在", out_schema=SbpSysOrgOutSchema
            ),
        )

    #: 树视图单次节点上限：组织表全量也只有千级，超过说明筛选条件没生效
    TREE_NODE_LIMIT = 5000

    async def tree(
        self,
        *,
        category: str | None = None,
        include_deleted: bool = False,
        include_disabled: bool = True,
    ) -> list[SbpSysOrgTreeNodeSchema]:
        """组织树：一次返回全量节点供页面展开。

        默认排除已逻辑删除、保留停用节点（停用是状态，不是「不存在」）；
        ``category`` 用于分行政/业务两棵树，两棵树不合并。
        """
        nodes = await self.crud.list_for_tree(
            category=category,
            include_deleted=include_deleted,
            include_disabled=include_disabled,
            limit=self.TREE_NODE_LIMIT,
        )
        if len(nodes) > self.TREE_NODE_LIMIT:
            raise CustomException(
                msg=(
                    f"组织节点超过 {self.TREE_NODE_LIMIT} 个，请按 category 分树查询；"
                    "不做静默截断"
                ),
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        return self._build_tree(nodes)

    @staticmethod
    def _build_tree(nodes: Sequence[SbpSysOrgModel]) -> list[SbpSysOrgTreeNodeSchema]:
        """平铺节点 → 森林。

        三条容错必须保留：``parent_id`` 指向不存在的节点、自引用、以及 A↔B 互指
        形成的环。前两者按根处理，环则从任意未访问节点起树并截断回边，
        否则整块节点会从视图里消失（脏数据在 GSI 侧并不可控）。
        """
        known_ids = {node.id for node in nodes}
        children_map: dict[int, list[SbpSysOrgModel]] = defaultdict(list)
        roots: list[SbpSysOrgModel] = []
        for node in nodes:
            parent_id = node.parent_id
            if parent_id and parent_id != node.id and parent_id in known_ids:
                children_map[parent_id].append(node)
            else:
                roots.append(node)

        visited: set[int] = set()

        def build(node: SbpSysOrgModel) -> SbpSysOrgTreeNodeSchema:
            visited.add(node.id)
            item = SbpSysOrgTreeNodeSchema.model_validate(node)
            item.children = [
                build(child)
                for child in children_map.get(node.id, [])
                if child.id not in visited
            ]
            return item

        forest = [build(node) for node in sorted(roots, key=lambda node: node.id)]
        for node in sorted(nodes, key=lambda node: node.id):
            if node.id not in visited:
                forest.append(build(node))
        return forest
