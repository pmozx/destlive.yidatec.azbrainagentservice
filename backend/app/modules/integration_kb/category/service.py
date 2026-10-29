"""知识分类服务：空间内二级树查询与维护（创建/改/删时联动文档归属）。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import CustomException

from ..document.model import IntegrationKbDocumentModel
from ..space.crud import IntegrationKbSpaceCRUD
from .crud import IntegrationKbCategoryCRUD
from .model import IntegrationKbCategoryModel
from .schema import CategoryCreateSchema, CategoryOutSchema, CategoryQueryParam, CategoryUpdateSchema


class IntegrationKbCategoryService:
    """知识分类服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = IntegrationKbCategoryCRUD(db)

    async def tree(self, search: CategoryQueryParam | None = None) -> list[CategoryOutSchema]:
        """查询分类树（space_id 为空时返回全部空间的树），节点附带直属文档数。"""
        rows = await self.crud.get_list(
            search={"status": ("eq", 0)} | ({"space_id": ("eq", search.space_id)} if search and search.space_id else {}),
            order_by=[{"sort": "asc"}, {"id": "asc"}],
        )
        if search and search.name:
            rows = [row for row in rows if search.name in (row.name or "")]
        roots = self._build_tree(rows)
        await self._fill_doc_count(roots, category_ids=[row.id for row in rows])
        return roots

    async def create(self, data: CategoryCreateSchema) -> CategoryOutSchema:
        """创建分类（空间必须存在；最多两级；同级名称唯一）。"""
        if not await IntegrationKbSpaceCRUD(self.db).get(id=data.space_id):
            raise CustomException(msg="创建失败，所属空间不存在")
        parent = await self._validate_parent(space_id=data.space_id, parent_id=data.parent_id)
        await self._validate_name_unique(space_id=data.space_id, parent_id=parent.id if parent else None, name=data.name)
        obj = await self.crud.create(data=data)
        return CategoryOutSchema.model_validate(obj)

    async def update(self, id: int, data: CategoryUpdateSchema) -> CategoryOutSchema:
        """修改分类（不允许自引用；最多两级；同级名称唯一）。"""
        await self.crud.get_or_404(id=id, msg="修改失败，知识分类不存在")
        if data.parent_id == id:
            raise CustomException(msg="修改失败，父分类不能是自身")
        space_id = data.space_id or (await self.crud.get_or_404(id=id)).space_id
        await self._validate_parent(space_id=space_id, parent_id=data.parent_id)
        exist = await self.crud.get(space_id=space_id, parent_id=data.parent_id, name=data.name)
        if exist and exist.id != id:
            raise CustomException(msg="修改失败，同级分类名称已存在")
        updated = await self.crud.update(id=id, data=data)
        return CategoryOutSchema.model_validate(updated)

    async def delete(self, ids: list[int]) -> None:
        """删除分类：子分类提升为一级，其下文档归入未分类（category_id 置空）。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        categories = await self.crud.get_list(search={"id": ("in", ids)})
        if len(categories) != len(set(ids)):
            raise CustomException(msg="删除失败，存在不存在的知识分类")

        children = await self.crud.get_list(search={"parent_id": ("in", ids)})
        if children:
            await self.crud.set(ids=[child.id for child in children], parent_id=None)
        await self.db.execute(
            update(IntegrationKbDocumentModel)
            .where(IntegrationKbDocumentModel.category_id.in_(ids), IntegrationKbDocumentModel.is_deleted.is_(False))
            .values(category_id=None)
        )
        await self.crud.delete(ids=ids)

    async def _validate_parent(self, space_id: int, parent_id: int | None) -> IntegrationKbCategoryModel | None:
        """校验父分类：存在、同空间且为一级分类（保证最多两级树）。"""
        if parent_id is None:
            return None
        parent = await self.crud.get(id=parent_id)
        if not parent:
            raise CustomException(msg="父分类不存在")
        if parent.space_id != space_id:
            raise CustomException(msg="父分类不属于目标空间")
        if parent.parent_id is not None:
            raise CustomException(msg="分类最多支持两级，不能在二级分类下创建子分类")
        return parent

    async def _validate_name_unique(self, space_id: int, parent_id: int | None, name: str) -> None:
        """校验同级分类名称唯一。"""
        if await self.crud.get(space_id=space_id, parent_id=parent_id, name=name):
            raise CustomException(msg="创建失败，同级分类名称已存在")

    async def _fill_doc_count(self, roots: list[CategoryOutSchema], category_ids: list[int]) -> None:
        """按分类聚合未删除直属文档数，递归填充树节点 doc_count。"""
        if not category_ids:
            return
        result = await self.db.execute(
            select(IntegrationKbDocumentModel.category_id, func.count().label("cnt"))
            .where(
                IntegrationKbDocumentModel.is_deleted.is_(False),
                IntegrationKbDocumentModel.category_id.in_(category_ids),
            )
            .group_by(IntegrationKbDocumentModel.category_id)
        )
        counts = {row.category_id: row.cnt for row in result.all()}

        def walk(nodes: list[CategoryOutSchema]) -> None:
            for node in nodes:
                node.doc_count = counts.get(node.id, 0)
                walk(node.children)

        walk(roots)

    @staticmethod
    def _build_tree(categories: list[IntegrationKbCategoryModel]) -> list[CategoryOutSchema]:
        """组装二级树（孤儿节点提升为根，保证树完整性）。"""
        nodes = {row.id: CategoryOutSchema.model_validate(row) for row in categories}
        roots: list[CategoryOutSchema] = []
        for row in categories:
            node = nodes[row.id]
            parent_node = nodes.get(row.parent_id) if row.parent_id else None
            if parent_node is not None and parent_node is not node:
                parent_node.children.append(node)
            else:
                roots.append(node)
        return roots
