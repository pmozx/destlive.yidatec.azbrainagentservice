"""GSI 职级出入参 Schema。"""

from pydantic import BaseModel, ConfigDict, Field


class SbpSysRankOutSchema(BaseModel):
    """职级输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="职级ID")
    rank_code: str = Field(..., description="职级编码")
    rank_name: str = Field(..., description="职级名称")
    sort_no: int = Field(..., description="排序号")
    status: str = Field(..., description="状态：1启用 0停用")
    deleted: int = Field(..., description="逻辑删除：0否 1是")


class SbpSysRankQueryParam(BaseModel):
    """职级查询参数。"""

    rank_code: str | None = Field(
        default=None, description="职级编码", json_schema_extra={"q": "eq"}
    )
    rank_name: str | None = Field(
        default=None, description="职级名称", json_schema_extra={"q": "like"}
    )
    status: str | None = Field(
        default=None, description="状态：1启用 0停用", json_schema_extra={"q": "eq"}
    )
    deleted: int | None = Field(
        default=None, description="逻辑删除：0否 1是", json_schema_extra={"q": "eq"}
    )
