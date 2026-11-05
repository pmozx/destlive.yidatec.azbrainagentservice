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
