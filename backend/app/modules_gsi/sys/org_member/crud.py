"""GSI 组织成员只读数据访问（含组织上下文联表查询）。"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from app.modules_gsi.base import SbpReadCRUD
from app.modules_gsi.sys.org.model import SbpSysOrgModel
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
        user_id: int | None = None,
        org_category: str | None = None,
        org_type: str | None = None,
        user_status: str | None = None,
        valid_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """成员 + 组织上下文 + 用户状态联表分页。

        两处 LEFT JOIN 都不能换成 INNER JOIN：组织或用户被物理清掉时，
        成员关系行仍要可见，缺失侧以 ``None`` 返回供页面提示。
        """
        org = SbpSysOrgModel
        user = SbpSysUserModel
        conditions = self._with_org_conditions(
            org_id=org_id,
            user_id=user_id,
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
                user.status.label("user_status"),
                user.allow_login.label("allow_login"),
                user.deleted.label("user_deleted"),
            )
            .select_from(self.model)
            .outerjoin(org, org.id == self.model.org_id)
            .outerjoin(user, user.id == self.model.user_id)
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

    def _with_org_conditions(
        self,
        *,
        org_id: int | None,
        user_id: int | None,
        org_category: str | None,
        org_type: str | None,
        user_status: str | None,
        valid_only: bool,
    ) -> list[ColumnElement]:
        """联表查询条件：空值一律按「不筛选」跳过。"""
        org = SbpSysOrgModel
        user = SbpSysUserModel
        conditions: list[ColumnElement] = []
        if org_id is not None:
            conditions.append(self.model.org_id == org_id)
        if user_id is not None:
            conditions.append(self.model.user_id == user_id)
        if org_category:
            conditions.append(org.category == org_category)
        if org_type:
            conditions.append(org.type == org_type)
        if user_status:
            conditions.append(user.status == user_status)
        if valid_only:
            conditions.extend([org.status == "1", org.deleted == 0])
        return conditions
