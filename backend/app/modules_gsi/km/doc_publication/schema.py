"""GSI 文档按团队生效关系出入参 Schema。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
