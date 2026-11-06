"""GSI 组织架构只读数据访问。"""

from sqlalchemy import select

from app.modules_gsi.base import SbpReadCRUD

from .model import SbpSysOrgModel


class SbpSysOrgCRUD(SbpReadCRUD[SbpSysOrgModel]):
    """``sbp_sys_org`` 读取。"""

    model = SbpSysOrgModel

    async def list_for_tree(
        self,
        *,
        category: str | None,
        include_deleted: bool,
        include_disabled: bool,
        limit: int,
    ) -> list[SbpSysOrgModel]:
        """取组织树全量节点。

        分页接口无法直接拼树（父节点可能落在别的页），所以树视图一次取全；
        多取一行用于判断超限，截断与否由 service 决定并报错，不做静默截断。
        """
        conditions = []
        if category:
            conditions.append(self.model.category == category)
        if not include_deleted:
            conditions.append(self.model.deleted == 0)
        if not include_disabled:
            conditions.append(self.model.status == "1")
        sql = (
            select(self.model)
            .where(*conditions)
            .order_by(self.model.parent_id.asc(), self.model.id.asc())
            .limit(limit + 1)
        )
        result = await self.db.execute(sql)
        return list(result.scalars().all())

    async def list_id_parent_pairs(self) -> list[tuple[int, int | None]]:
        """取 ``(id, parent_id)`` 两列，用于把组织展开成子树 id 集合。

        只读两列：展开「含下级」不需要名称/状态，整表扫一遍也比递归 CTE 更好测、
        更容易把脏数据（自引用、互指成环）在纯函数里处理掉。
        """
        sql = select(self.model.id, self.model.parent_id)
        result = await self.db.execute(sql)
        return [(row[0], row[1]) for row in result.all()]
