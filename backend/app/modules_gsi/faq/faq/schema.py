"""GSI FAQ 主记录出入参 Schema。

只能展示编号与状态：问题/答案正文虽在 DDL 中存在，但契约未授权，
要展示必须由 DBA 扩权并回写《01-知识平台与AI接口对接说明》§四。
"""

from pydantic import BaseModel, ConfigDict, Field


class SbpFaqOutSchema(BaseModel):
    """FAQ 主记录输出（仅契约允许字段）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="FAQ 主键")
    faq_no: str | None = Field(default=None, description="FAQ 编号：FAQ-0000001")
    status: str = Field(..., description="业务状态：Draft/Published/Unpublished 等")
    deleted: int = Field(..., description="逻辑删除：0否 1是")


class SbpFaqQueryParam(BaseModel):
    """FAQ 主记录查询参数。"""

    faq_no: str | None = Field(
        default=None, description="FAQ 编号（精确）", json_schema_extra={"q": "eq"}
    )
    status: str | None = Field(
        default=None, description="业务状态", json_schema_extra={"q": "eq"}
    )
    deleted: int | None = Field(
        default=None, description="逻辑删除：0否 1是", json_schema_extra={"q": "eq"}
    )
