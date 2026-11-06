"""GSI 组织成员只读服务。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import PageResultSchema
from app.modules_gsi.sys.org.service import SbpSysOrgService
from app.utils.common_util import search_to_dict

from .crud import SbpSysOrgMemberCRUD
from .schema import (
    SbpSysOrgMemberOutSchema,
    SbpSysOrgMemberQueryParam,
    SbpSysOrgMemberWithOrgOutSchema,
    SbpSysOrgMemberWithOrgQueryParam,
    SbpUserOrgOutSchema,
    SbpUserOrgsOutSchema,
)


class SbpSysOrgMemberService:
    """``sbp_sys_org_member`` 查询服务，不包含任何写逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self.crud = SbpSysOrgMemberCRUD(db)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysOrgMemberQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SbpSysOrgMemberOutSchema]:
        """分页查询组织成员。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"org_id": "asc"}, {"user_id": "asc"}],
            search=search_to_dict(search),
            out_schema=SbpSysOrgMemberOutSchema,
        )

    async def page_with_org(
        self,
        page_no: int,
        page_size: int,
        search: SbpSysOrgMemberWithOrgQueryParam | None = None,
    ) -> PageResultSchema[SbpSysOrgMemberWithOrgOutSchema]:
        """组织成员页：成员 + 组织名称/编码/分类 + 用户有效性。

        ``include_sub`` 为真时先把 org_id 展开成子树 id 集合，再交给联表查询；
        未选组织（org_id 为空）时整表全量，不受 include_sub 影响。
        """
        params = search or SbpSysOrgMemberWithOrgQueryParam()
        org_ids = None
        if params.org_id is not None and params.include_sub:
            org_ids = await SbpSysOrgService(self.crud.db).subtree_ids(params.org_id)
        offset = (page_no - 1) * page_size
        rows, total = await self.crud.page_with_org(
            offset=offset,
            limit=page_size,
            org_id=params.org_id,
            org_ids=org_ids,
            user_id=params.user_id,
            username=params.username,
            cn_name=params.cn_name,
            org_category=params.org_category,
            org_type=params.org_type,
            user_status=params.user_status,
            valid_only=params.valid_only,
        )
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=[SbpSysOrgMemberWithOrgOutSchema.model_validate(row) for row in rows],
        )

    #: 层级路径向上追溯的层数上限：脏数据可能互指成环，不能无限向上
    ORG_PATH_DEPTH_LIMIT = 20

    async def user_orgs(self, user_id: int) -> SbpUserOrgsOutSchema:
        """按用户反查组织归属（含层级路径与用户档案）。

        详情弹窗以用户为主体，因此一次返回「用户是谁 + 他挂在哪些部门」；
        组织缺失（物理删除）的行保留，``org_name`` 为空，页面提示而非隐藏。
        """
        rows = await self.crud.list_orgs_by_user(user_id)
        paths = await self._resolve_org_paths(rows)
        orgs = [
            SbpUserOrgOutSchema.model_validate(
                {**row, "org_path": paths.get(row["org_id"], "")}
            )
            for row in rows
        ]
        # 延迟导入：user.crud 为了统计组织/团队归属数要 import 本包的 model，
        # 模块级互相 import 会在 sys 包初始化时撞成循环导入。
        from app.modules_gsi.sys.user.crud import SbpSysUserCRUD

        user = await SbpSysUserCRUD(self.crud.db).get_with_rank(user_id)
        return SbpUserOrgsOutSchema(
            user_id=user_id,
            user_exists=user is not None,
            user_username=user["username"] if user else None,
            user_cn_name=user["cn_name"] if user else None,
            user_status=user["status"] if user else None,
            allow_login=user["allow_login"] if user else None,
            user_deleted=user["deleted"] if user else None,
            org_count=len(orgs),
            valid_org_count=sum(1 for org in orgs if org.org_valid),
            orgs=orgs,
        )

    async def _resolve_org_paths(
        self, rows: list[dict[str, Any]]
    ) -> dict[int, str]:
        """组织 ID → 层级路径（顶级 → 本节点）。

        只逐层向上补祖先（每层一条批量查询），不拉全量组织表；
        悬挂 parent 与成环均由「已取到的节点」与深度上限自然截断。
        """
        names: dict[int, tuple[str, int]] = {
            row["org_id"]: (row["org_name"], row["org_parent_id"] or 0)
            for row in rows
            if row["org_name"] is not None
        }
        depth = 0
        frontier = {parent for _, parent in names.values() if parent}
        while frontier and depth < self.ORG_PATH_DEPTH_LIMIT:
            pending = [org_id for org_id in frontier if org_id not in names]
            if not pending:
                break
            briefs = await self.crud.list_org_briefs(pending)
            if not briefs:
                break
            for org in briefs:
                names.setdefault(org.id, (org.name, org.parent_id or 0))
            frontier = {org.parent_id for org in briefs if org.parent_id}
            depth += 1
        return {
            org_id: self._compose_path(org_id, names) for org_id in names
        }

    @staticmethod
    def _compose_path(org_id: int, names: dict[int, tuple[str, int]]) -> str:
        """从当前节点向上拼接名称；成环或缺失祖先时在断点处停止。"""
        parts: list[str] = []
        seen: set[int] = set()
        current = org_id
        while current and current not in seen:
            seen.add(current)
            node = names.get(current)
            if node is None:
                break
            name, parent_id = node
            parts.append(name)
            current = parent_id
        return " / ".join(reversed(parts))
