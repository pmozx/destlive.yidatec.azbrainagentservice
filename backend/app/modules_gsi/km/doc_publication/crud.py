"""GSI 文档按团队生效关系只读数据访问。"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, func, select

from app.modules_gsi.base import SbpReadCRUD
from app.modules_gsi.km.doc.model import SbpKmDocModel
from app.modules_gsi.km.doc_record.model import SbpKmDocRecordModel
from app.modules_gsi.sys.audience_team.model import SbpSysAudienceTeamModel
from app.modules_gsi.sys.audience_team_member.model import SbpSysAudienceTeamMemberModel

from .model import SbpKmDocPublicationModel
from .schema import AuthorizedCandidate

#: 主记录被置为这两类状态即不再对外提供内容（逻辑删除另看 ``deleted``）
DOC_REJECTED_STATUSES: tuple[str, ...] = ("Archived", "Unpublished")


class SbpKmDocPublicationCRUD(SbpReadCRUD[SbpKmDocPublicationModel]):
    """``sbp_km_doc_publication`` 读取。"""

    model = SbpKmDocPublicationModel

    async def list_member_team_keys(self, *, user_id: int) -> set[str]:
        """用户仍有效的受众团队编码集合（授权与 ``invisibleTeams`` 排除共用同一份）。

        链路（《03-服务大脑GSI账号组织关系与知识权限治理说明》§1.5）：
        ``用户 → audience_team_member.team_id → audience_team.group_code``。

        - 授权匹配只认 ``group_code``：``audience_team`` 列仅兼容历史标识，不参与判定；
        - 停用（``status<>'1'``）与逻辑删除（``deleted<>0``）的团队不产生权限；
        - 黑名单判断也用这一份：拿别的标识去比会让「命中不可见团队」漏掉。
        """
        sql = (
            select(SbpSysAudienceTeamModel.group_code)
            .join(
                SbpSysAudienceTeamMemberModel,
                SbpSysAudienceTeamMemberModel.team_id == SbpSysAudienceTeamModel.id,
            )
            .where(
                SbpSysAudienceTeamMemberModel.user_id == user_id,
                SbpSysAudienceTeamModel.status == "1",
                SbpSysAudienceTeamModel.deleted == 0,
            )
            .distinct()
        )
        result = await self.db.execute(sql)
        return {row[0] for row in result.all() if row[0]}

    async def list_authorized_candidates(self, *, team_keys: Sequence[str]) -> list[AuthorizedCandidate]:
        """团队集合 → 候选发布单元（含生效快照的规则 JSON 与主记录状态）。

        三个 JOIN 缺一不可（契约 §四「候选数据必须继续执行的判断」第 3、4 步）：

        - ``sbp_km_doc``：只要 ``deleted=0`` 且未归档 / 未下架；不能要求
          ``status='Published'``——修订审批中旧发布关系仍指向旧快照，旧内容应当继续可用；
        - ``sbp_km_doc_record``：必须是 ``publication.record_id`` 指向的那一行，
          只有这一行的 ``snapshot_json`` 才是生效规则；
        - ``snapshot_json`` 是 ``text``（不是 jsonb），黑名单 / 有效期只能在应用层解析。
        """
        keys = [key for key in team_keys if key]
        if not keys:
            # 空集不能下发 ``IN ()``：调用方据此直接返回空命中，绝不表示全库
            return []
        sql = (
            select(
                SbpKmDocPublicationModel.id,
                SbpKmDocPublicationModel.doc_id,
                SbpKmDocPublicationModel.record_id,
                SbpKmDocRecordModel.snapshot_json,
            )
            .select_from(SbpKmDocPublicationModel)
            .join(
                SbpKmDocModel,
                and_(
                    SbpKmDocModel.id == SbpKmDocPublicationModel.doc_id,
                    SbpKmDocModel.deleted == 0,
                    SbpKmDocModel.status.notin_(DOC_REJECTED_STATUSES),
                ),
            )
            .join(
                SbpKmDocRecordModel,
                and_(
                    SbpKmDocRecordModel.id == SbpKmDocPublicationModel.record_id,
                    SbpKmDocRecordModel.doc_id == SbpKmDocPublicationModel.doc_id,
                ),
            )
            .where(SbpKmDocPublicationModel.team.in_(keys))
            .distinct()
        )
        result = await self.db.execute(sql)
        return [
            AuthorizedCandidate(
                publication_id=publication_id,
                doc_id=doc_id,
                record_id=record_id,
                snapshot_json=snapshot_json or "",
            )
            for publication_id, doc_id, record_id, snapshot_json in result.all()
            if publication_id
        ]

    async def list_by_ids(self, publication_ids: Sequence[str]) -> list[SbpKmDocPublicationModel]:
        """批量读发布单元（一次 IN 查询，避免按 ID 逐个 round-trip）。

        只读生效关系本身的字段（``doc_id`` / ``record_id`` / ``team`` / ``index_status``），
        不做授权过滤：调用方（治理链）传入的 ID 来自已校验过的 scope，越权判定不在这里重复。
        空入参直接返回空列表，不能把 ``IN ()`` 发到数据库。
        """
        if not publication_ids:
            return []
        sql = select(SbpKmDocPublicationModel).where(SbpKmDocPublicationModel.id.in_(list(publication_ids)))
        result = await self.db.execute(sql)
        return list(result.scalars().all())

    async def map_team_names(self, publication_ids: Sequence[str]) -> dict[str, list[str]]:
        """发布单元 → 受众团队名称（仅 ``status='1'`` 且 ``deleted=0`` 的团队）。

        匹配只认 ``group_code``（与授权口径一致）；停用/逻辑删除的团队不产生名称，
        否则会把「已失效团队」展示成当前可见范围，误导跨受众合并判断。
        """
        if not publication_ids:
            return {}
        sql = (
            select(SbpKmDocPublicationModel.id, SbpSysAudienceTeamModel.group_name)
            .join(
                SbpSysAudienceTeamModel,
                and_(
                    SbpSysAudienceTeamModel.group_code == SbpKmDocPublicationModel.team,
                    SbpSysAudienceTeamModel.status == "1",
                    SbpSysAudienceTeamModel.deleted == 0,
                ),
            )
            .where(SbpKmDocPublicationModel.id.in_(list(publication_ids)))
            .distinct()
        )
        result = await self.db.execute(sql)
        teams: dict[str, list[str]] = {}
        for publication_id, group_name in result.all():
            if not publication_id or not group_name:
                continue
            names = teams.setdefault(publication_id, [])
            if group_name not in names:
                names.append(group_name)
        return teams

    async def page_with_team(
        self,
        *,
        offset: int,
        limit: int,
        doc_id: str | None = None,
        team: str | None = None,
        record_id: str | None = None,
        index_status: str | None = None,
        team_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """生效关系 + 团队名称联表分页。

        连接条件只认 ``group_code``（与授权判定同口径），且用 LEFT JOIN：
        ``team`` 存了名称或旧标识时不会命中，这种行必须能显示出来并标记
        ``team_matched=false``，否则「发布单元存在但用户永远看不到」会被页面藏掉。
        """
        team_model = SbpSysAudienceTeamModel
        conditions = []
        if doc_id:
            conditions.append(self.model.doc_id == doc_id)
        if team:
            conditions.append(self.model.team == team)
        if record_id:
            conditions.append(self.model.record_id == record_id)
        if index_status:
            conditions.append(self.model.index_status == index_status)
        if team_name:
            conditions.append(team_model.group_name.like(f"%{team_name}%"))
        joins = (
            select(
                self.model.id.label("id"),
                self.model.doc_id.label("doc_id"),
                self.model.team.label("team"),
                team_model.group_name.label("team_name"),
                team_model.audience_team.label("team_ident"),
                team_model.status.label("team_status"),
                team_model.deleted.label("team_deleted"),
                self.model.record_id.label("record_id"),
                self.model.index_status.label("index_status"),
                self.model.updated_at.label("updated_at"),
            )
            .select_from(self.model)
            .outerjoin(team_model, team_model.group_code == self.model.team)
        )
        count_sql = (
            select(func.count())
            .select_from(self.model)
            .outerjoin(team_model, team_model.group_code == self.model.team)
            .where(*conditions)
        )
        data_sql = joins.where(*conditions).order_by(
            self.model.updated_at.desc(), self.model.id.asc()
        )
        return await self.fetch_rows(
            data_sql=data_sql, count_sql=count_sql, offset=offset, limit=limit
        )
