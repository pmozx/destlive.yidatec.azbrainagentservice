"""`#01` 提交解析任务的请求 Schema（手册 §六.01 字段逐条对应）。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validator import ensure_non_blank


class ParseOptions(BaseModel):
    """`options`：当前 GSI 固定传 ``chunk=true``。"""

    chunk: bool = Field(description="是否生成切片")
    language: str = Field(description="解析语言，与 metadata.language 保持一致")

    @field_validator("language")
    @classmethod
    def _language_required(cls, value: str) -> str:
        return ensure_non_blank(value)


class ParseJobRequest(BaseModel):
    """`#01` 请求正文（手册 §六.01 字段逐条对应）。"""

    model_config = ConfigDict(extra="ignore")

    job_id: str = Field(description="GSI 预先生成的解析任务 ID，也是幂等标识")
    doc_id: str = Field(description="文档 / 本次解析所属业务资源 ID")
    version_id: str = Field(description="业务版本标识（字符串，不按数字比较）")
    file_url: str = Field(description="AI 可直接读取的文件 URL（http/https）")
    file_name: str = Field(description="原始文件名，带扩展名")
    options: ParseOptions
    tenant_id: str = Field(description="租户标识，当前固定 gsi-default")
    resource_type: str = Field(description="document / faq")
    metadata: dict[str, Any] = Field(description="页面已保存的元数据快照（解析上下文）")
    file_id: str | None = Field(default=None, description="GSI 文件标识（仅追踪用）")
    file_size: int | None = Field(default=None, ge=0, description="文件大小（字节）")
    prompt_text: str | None = Field(default=None, description="resource_type=faq 时必填")
    model_key: str | None = Field(default=None, description="resource_type=faq 时必填")

    @field_validator("job_id", "doc_id", "version_id", "file_url", "file_name", "tenant_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("prompt_text", "model_key")
    @classmethod
    def _optional_text_not_blank(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        if value not in ("document", "faq"):
            raise ValueError("resource_type 只支持 document / faq")
        return value

    @model_validator(mode="after")
    def _faq_requires_prompt_and_model(self) -> "ParseJobRequest":
        if self.resource_type == "faq":
            for name in ("prompt_text", "model_key"):
                value = getattr(self, name)
                if not value or not value.strip():
                    raise ValueError(f"resource_type 为 faq 时，{name} 必填且不能仅包含空白字符。")
        return self


class ParseJobReceipt(BaseModel):
    """`#01` 202 受理回执。"""

    job_id: str
    status: str
    message: str


class ParseJobStatus(BaseModel):
    """`#02` 任务快照：成功带 result，失败带 error_code（任务失败 ≠ 请求失败）。"""

    job_id: str
    status: str
    progress: int
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

