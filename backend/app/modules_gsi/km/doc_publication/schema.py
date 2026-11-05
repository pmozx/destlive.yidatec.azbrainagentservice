"""GSI 文档按团队生效关系出入参 Schema。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


def publication_subtitle(teams: Sequence[str]) -> str:
    """受众团队 → 展示副标题，形如「正式知识 · IT团队」；无团队时只回「正式知识」。

    下线为模块函数而不是只做属性：治理分组可能把多个团队的发布单元合成一条知识，
    调用方会先把团队名合并去重再取文案，两处必须用同一口径，不能各拼一份字符串。
    """
    return "正式知识 · " + "、".join(teams) if teams else "正式知识"


@dataclass(frozen=True)
class SbpKmDocPublicationBrief:
    """发布单元的业务展示信息（治理页 ``items[].subtitle`` 的取数来源）。

    只带「展示与比对需要的那几列」，不把整行交给上层：GSI 业务表由 GSI 维护，
    AI 侧只用它补展示，不应依赖其它列。
    """

    publication_id: str
    doc_id: str
    record_id: str
    index_status: str
    teams: tuple[str, ...] = ()

    @property
    def subtitle(self) -> str:
        """单个发布单元的展示副标题（口径见 :func:`publication_subtitle`）。"""
        return publication_subtitle(self.teams)


class SbpKmDocPublicationOutSchema(BaseModel):
    """知识按受众团队生效内容输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="生效记录主键")
    doc_id: str = Field(..., description="知识主键")
    team: str = Field(..., description="获授权受众团队")
    record_id: str = Field(..., description="当前生效的整篇送审快照")
    index_status: str = Field(..., description="索引同步状态：pending/succeeded/failed")
    updated_at: datetime = Field(..., description="生效更新时间")


class SbpKmDocPublicationQueryParam(BaseModel):
    """文档按团队生效关系查询参数。"""

    doc_id: str | None = Field(
        default=None, description="知识主键", json_schema_extra={"q": "eq"}
    )
    team: str | None = Field(
        default=None, description="获授权受众团队", json_schema_extra={"q": "eq"}
    )
    record_id: str | None = Field(
        default=None, description="生效快照主键", json_schema_extra={"q": "eq"}
    )
    index_status: str | None = Field(
        default=None, description="索引同步状态", json_schema_extra={"q": "eq"}
    )


class SbpKmDocPublicationWithTeamOutSchema(BaseModel):
    """生效关系 + 团队标识解析结果。

    ``team`` 存的是团队编码（``group_code``），页面必须能同时看到名称才知道是哪一支；
    ``team_matched=false`` 说明该行 ``team`` 在受众团队表里没命中编码——典型的历史名称/
    兼容标识数据，权限判定会直接落空，必验。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="生效记录主键")
    doc_id: str = Field(..., description="知识主键")
    team: str = Field(..., description="获授权受众团队（存储值为 group_code）")
    team_name: str | None = Field(default=None, description="团队名称")
    team_ident: str | None = Field(default=None, description="受众团队兼容标识")
    team_status: str | None = Field(default=None, description="团队状态：1启用 0停用")
    team_deleted: int | None = Field(default=None, description="团队逻辑删除：0否 1是")
    team_matched: bool = Field(default=False, description="team 是否在受众团队表中命中编码")
    team_valid: bool = Field(default=False, description="命中且团队有效：status=1 且 deleted=0")
    record_id: str = Field(..., description="当前生效的整篇送审快照")
    index_status: str = Field(..., description="索引同步状态：pending/succeeded/failed")
    updated_at: str | None = Field(default=None, description="生效更新时间（业务本地时间）")

    @model_validator(mode="after")
    def _derive_match_flag(self) -> "SbpKmDocPublicationWithTeamOutSchema":
        self.team_matched = self.team_status is not None
        self.team_valid = (
            self.team_matched and self.team_status == "1" and self.team_deleted == 0
        )
        return self


class SbpKmDocPublicationWithTeamQueryParam(BaseModel):
    """生效关系（含团队名称）查询参数。"""

    doc_id: str | None = Field(default=None, description="知识主键（精确）")
    team: str | None = Field(default=None, description="团队编码（精确）")
    record_id: str | None = Field(default=None, description="生效快照主键（精确）")
    index_status: str | None = Field(default=None, description="索引同步状态")
    team_name: str | None = Field(default=None, description="团队名称（模糊）")
