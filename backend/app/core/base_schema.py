import json
from typing import Any

from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.core.validator import DateTimeStr

class BaseSchema(BaseModel):
    """通用输出模型，包含基础字段和审计字段"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = Field(default=None, description="主键ID")
    uuid: str | None = Field(default=None, description="UUID")
    created_time: DateTimeStr | None = Field(default=None, description="创建时间")
    updated_time: DateTimeStr | None = Field(default=None, description="更新时间")


class PageResultSchema[T](BaseModel):
    """分页查询结果模型"""

    model_config = ConfigDict(from_attributes=True)

    page_no: int | None = Field(default=None, ge=1, description="页码，默认为1")
    page_size: int | None = Field(default=None, ge=1, description="页面大小，默认为10")
    total: int = Field(default=0, ge=0, description="总记录数")
    has_next: bool | None = Field(default=False, description="是否有下一页")
    items: list[T] = Field(default_factory=list, description="分页后的数据列表")

class PaginationQueryParam(BaseModel):
    """分页 —— order_by 以 JSON 字符串传递，避免 Depends() 模式下 list 字段被当 body 验证。"""

    page_no: int = Field(default=1, description="当前页码", ge=1)
    page_size: int = Field(default=10, description="每页数量", ge=1, le=100)
    order_by: Any = Field(
        default=None,
        description="排序字段 JSON 字符串, 格式:[{'field1': 'asc'}, {'field2': 'desc'}]",
    )

    @field_validator("order_by")
    @classmethod
    def validate_order_by(cls, v: Any) -> Any:
        """校验 order_by：None→默认升序，str→json.loads 转 list，list→直接返回，其他→抛异常。"""
        if v is None:
            return [{"id": "asc"}]
        if isinstance(v, str):
            try:
                result = json.loads(v)
                if not isinstance(result, list):
                    raise ValueError("order_by 必须是 JSON 数组字符串，例如 [{\"id\":\"asc\"}]")
                return result
            except json.JSONDecodeError:
                raise ValueError("order_by 字符串无法解析为 JSON，请传入有效的 JSON 数组字符串，例如 [{\"id\":\"asc\"}]")
        if isinstance(v, list):
            return v
        raise ValueError(f"order_by 类型无效: {type(v).__name__}，预期为 JSON 数组字符串或列表")


class BaseQueryParam(BaseModel):
    """created_time + updated_time —— 子类自动继承

    前端传数组格式 ``["start", "end"]``，``search_to_dict`` 自动转为 ``("between", [start, end])``。
    """

    created_time: list[DateTimeStr] | None = Field(None, description="创建时间范围")
    updated_time: list[DateTimeStr] | None = Field(None, description="更新时间范围")