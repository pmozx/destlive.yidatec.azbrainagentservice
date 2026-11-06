"""GSI 组织成员只读数据访问（含组织上下文联表查询）。"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from app.modules_gsi.base import SbpReadCRUD
from app.modules_gsi.sys.org.model import SbpSysOrgModel
from app.modules_gsi.sys.rank.model import SbpSysRankModel
from app.modules_gsi.sys.user.model import SbpSysUserModel

from .model import SbpSysOrgMemberModel


class SbpSysOrgMemberCRUD(SbpReadCRUD[SbpSysOrgMemberModel]):
    """``sbp_sys_org_member`` 读取。"""

    model = SbpSysOrgMemberModel

    async def page_with_org(
        self,
        *,
        offset: int,
        limit: int,
        org_id: int | None = None,
        org_ids: list[int] | None = None,
        user_id: int | None = None,
        username: str | None = None,
        cn_name: str | None = None,
        org_category: str | None = None,
        org_type: str | None = None,
        user_status: str | None = None,
        valid_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """成员 + 组织上下文 + 用户档案联表分页。

        ``org_ids`` 优先于 ``org_id``：前者是「含下级」展开后的子树 id 集合，
        后者是单节点精确匹配。

        三处 LEFT JOIN 都不能换成 INNER JOIN：组织、用户或职级被物理清掉时，
        成员关系行仍要可见，缺失侧以 ``None`` 返回供页面提示。
        """
        org = SbpSysOrgModel
        user = SbpSysUserModel
        rank = SbpSysRankModel
        conditions = self._with_org_conditions(
            org_id=org_id,
            org_ids=org_ids,
            user_id=user_id,
            username=username,
            cn_name=cn_name,
            org_category=org_category,
            org_type=org_type,
            user_status=user_status,
            valid_only=valid_only,
        )
        joins = (
            select(
                self.model.org_id.label("org_id"),
                org.name.label("org_name"),
                org.code.label("org_code"),
                org.type.label("org_type"),
                org.category.label("org_category"),
                org.parent_id.label("org_parent_id"),
                org.status.label("org_status"),
                org.deleted.label("org_deleted"),
                self.model.user_id.label("user_id"),
                user.username.label("user_username"),
                user.cn_name.label("user_cn_name"),
                user.en_name.label("user_en_name"),
                user.position_name.label("user_position_name"),
                user.rank_id.label("user_rank_id"),
                rank.rank_name.label("user_rank_name"),
                user.status.label("user_status"),
                user.allow_login.label("allow_login"),
                user.deleted.label("user_deleted"),
            )
            .select_from(self.model)
            .outerjoin(org, org.id == self.model.org_id)
            .outerjoin(user, user.id == self.model.user_id)
            .outerjoin(rank, rank.id == user.rank_id)
        )
        count_sql = (
            select(func.count())
            .select_from(self.model)
            .outerjoin(org, org.id == self.model.org_id)
            .outerjoin(user, user.id == self.model.user_id)
            .where(*conditions)
        )
        data_sql = joins.where(*conditions).order_by(
            org.id.asc(), self.model.user_id.asc()
        )
        return await self.fetch_rows(
            data_sql=data_sql, count_sql=count_sql, offset=offset, limit=limit
        )

    async def list_orgs_by_user(self, user_id: int) -> list[dict[str, Any]]:
        """用户 → 全部组织归属。

        同样不筛 ``status`` / ``deleted``：详情页要能解释「组织被停用/删除」
        与「根本没有组织归属」是两种完全不同的结论。
        """
        org = SbpSysOrgModel
        sql = (
            select(
                self.model.org_id.label("org_id"),
                org.name.label("org_name"),
                org.code.label("org_code"),
                org.type.label("org_type"),
                org.category.label("org_category"),
                org.parent_id.label("org_parent_id"),
                org.status.label("org_status"),
                org.deleted.label("org_deleted"),
            )
            .select_from(self.model)
            .outerjoin(org, org.id == self.model.org_id)
            .where(self.model.user_id == user_id)
            .order_by(org.id.asc())
        )
        result = await self.db.execute(sql)
        return [dict(row) for row in result.mappings().all()]

    async def list_org_briefs(self, org_ids: list[int]) -> list[SbpSysOrgModel]:
        """按 ID 批量取组织（用于拼层级路径时向上补祖先）。"""
        if not org_ids:
            return []
        sql = select(SbpSysOrgModel).where(SbpSysOrgModel.id.in_(org_ids))
        result = await self.db.execute(sql)
        return list(result.scalars().all())

    def _with_org_conditions(
        self,
        *,
        org_id: int | None,
        user_id: int | None,
        username: str | None,
        cn_name: str | None,
        org_category: str | None,
        org_type: str | None,
        user_status: str | None,
        valid_only: bool,
        org_ids: list[int] | None = None,
    ) -> list[ColumnElement]:
        """联表查询条件：空值一律按「不筛选」跳过。"""
        org = SbpSysOrgModel
        user = SbpSysUserModel
        conditions: list[ColumnElement] = []
        if org_ids is not None:
            conditions.append(self.model.org_id.in_(org_ids))
        elif org_id is not None:
            conditions.append(self.model.org_id == org_id)
        if user_id is not None:
            conditions.append(self.model.user_id == user_id)
        if username:
            conditions.append(user.username.like(f"%{username}%"))
        if cn_name:
            conditions.append(user.cn_name.like(f"%{cn_name}%"))
        if org_category:
            conditions.append(org.category == org_category)
        if org_type:
            conditions.append(org.type == org_type)
        if user_status:
            conditions.append(user.status == user_status)
        if valid_only:
            conditions.extend([org.status == "1", org.deleted == 0])
        return conditions
