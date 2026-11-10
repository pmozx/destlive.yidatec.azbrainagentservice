"""模型配置数据访问：列表/分页/增删改查 + 取启用行。

与 ``CRUDBase`` 的差别只有一点：``list_active`` 是给 ``app.core.agents.registry`` 用的
「全量启用行」查询——取档案要的是整张表（十几行），不走分页也不叠加筛选条件。
三类模型共用一张表，所以快照必须包含全部类型：对话行取档案，嵌入/重排序行供客户端工厂使用。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import SystemModelConfigModel
from .schema import SystemModelConfigCreateSchema, SystemModelConfigUpdateSchema


class SystemModelConfigCRUD(
    CRUDBase[SystemModelConfigModel, SystemModelConfigCreateSchema, SystemModelConfigUpdateSchema]
):
    """模型配置 CRUD（写操作只 flush，事务边界在 HTTP 层）。"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(SystemModelConfigModel, db)

    async def list_active(self) -> Sequence[SystemModelConfigModel]:
        """全部启用行（注册表快照用）：按排序号、主键升序，保证默认模型选择稳定。"""
        sql = (
            select(self.model)
            .where(self.model.enabled.is_(True))
            .order_by(self.model.sort.asc(), self.model.id.asc())
        )
        result = await self.db.execute(sql)
        return result.scalars().all()

    async def get_by_config_key(
        self, model_config_key: str, *, exclude_id: int | None = None
    ) -> SystemModelConfigModel | None:
        """按模型标识取行（唯一性校验用；``exclude_id`` 用于更新时排除自己）。"""
        sql = select(self.model).where(self.model.model_config_key == model_config_key)
        if exclude_id is not None:
            sql = sql.where(self.model.id != exclude_id)
        result = await self.db.execute(sql)
        return result.scalars().first()

    async def clear_other_defaults(self, model_type: str, *, keep_id: int | None = None) -> None:
        """把同类型里除 ``keep_id`` 之外的默认标记清掉：默认模型是「每类一个」的单例。

        只清同类型：一个对话模型的默认身份与嵌入模型的默认身份互不影响——把嵌入行设默认时
        顺手把对话行也清掉，问答就会在无人察觉时回落到 env 配置。

        表只有十几行，直接全量扫一遍：单条 UPDATE 带条件反而要拼出 ``id != x`` 这种
        没人看得懂的 WHERE。
        """
        changed = False
        for row in await self.get_list():
            if row.is_default and row.id != keep_id and row.model_type == model_type:
                row.is_default = False
                changed = True
        if changed:
            await self.db.flush()
