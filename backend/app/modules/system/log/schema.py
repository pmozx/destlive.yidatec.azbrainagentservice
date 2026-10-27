from pydantic import BaseModel, Field

from app.core.base_schema import BaseQueryParam, BaseSchema


class OperationLogCreateSchema(BaseModel):
    """审计写入模型（字段与 OperationLogRecord / OperationLogModel 保持一致）"""

    username: str = Field(default="anonymous", max_length=64, description="操作人")
    status: int = Field(default=0, ge=0, le=1, description="操作状态(0:成功 1:失败)")
    description: str | None = Field(default=None, description="操作名称/异常摘要")
    request_path: str = Field(..., max_length=255, description="请求路径")
    request_method: str = Field(..., max_length=10, description="请求方式")
    request_query: str | None = Field(default=None, description="查询字符串")
    request_payload: str | None = Field(default=None, description="请求参数")
    response_code: int = Field(default=200, ge=100, le=599, description="响应状态码")
    response_json: str | None = Field(default=None, description="响应体")
    process_time: str | None = Field(default=None, max_length=20, description="处理耗时(毫秒)")
    request_ip: str | None = Field(default=None, max_length=50, description="请求IP")
    user_agent: str | None = Field(default=None, max_length=255, description="浏览器 UA")


class OperationLogOutSchema(BaseSchema):
    """操作日志列表响应（不返回请求/响应大字段，避免列表页数据量爆炸）"""

    username: str = Field(..., description="操作人")
    status: int = Field(default=0, description="操作状态(0:成功 1:失败)")
    description: str | None = Field(default=None, description="操作名称/异常摘要")
    request_path: str = Field(..., description="请求路径")
    request_method: str = Field(..., description="请求方式")
    response_code: int = Field(..., description="响应状态码")
    process_time: str | None = Field(default=None, description="处理耗时(毫秒)")
    request_ip: str | None = Field(default=None, description="请求IP")


class OperationLogDetailOutSchema(OperationLogOutSchema):
    """操作日志详情响应（含请求/响应体）"""

    request_query: str | None = Field(default=None, description="查询字符串")
    request_payload: str | None = Field(default=None, description="请求参数")
    response_json: str | None = Field(default=None, description="响应体")
    user_agent: str | None = Field(default=None, description="浏览器 UA")


class OperationLogQueryParam(BaseQueryParam):
    """操作日志查询参数（json_schema_extra 中的 q 决定 CRUD 层比较方式）"""

    request_path: str | None = Field(None, description="请求路径", json_schema_extra={"q": "like"})
    request_method: str | None = Field(None, description="请求方式", json_schema_extra={"q": "eq"})
    username: str | None = Field(None, description="操作人", json_schema_extra={"q": "like"})
    status: int | None = Field(None, ge=0, le=1, description="操作状态(0:成功 1:失败)", json_schema_extra={"q": "eq"})
    response_code: int | None = Field(None, ge=100, le=599, description="响应状态码", json_schema_extra={"q": "eq"})
    request_ip: str | None = Field(None, description="请求IP", json_schema_extra={"q": "like"})


class OperationLogCleanSchema(BaseModel):
    """过期清理参数"""

    days: int = Field(default=90, ge=1, le=3650, description="保留天数，早于该天数的日志将被物理删除")
