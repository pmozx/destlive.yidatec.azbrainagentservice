"""模型配置的出入参模型。

三条与安全和口径有关的约定：

1. **完整密钥不出接口**：出参只有 ``api_key_masked`` / ``has_api_key``，明文只在服务端存在；
2. **留空 = 不改**（仅更新时）：``api_key`` 传空串表示沿用库里的原值，传新值才覆盖——
   否则页面每次保存都得把密钥重新贴一遍，运维迟早会把掩码串存回去；
3. **参数白名单跟类型走**：chat / embedding / rerank 的可调参数完全不同，用错白名单等于
   让配置静默失效（读取侧会丢弃不认识的键），所以校验必须按 ``model_type`` 分流。
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.agents.profiles import ALLOWED_CONFIG_KEYS
from app.core.base_schema import BaseQueryParam, BaseSchema

from .model import MODEL_TYPE_CHAT, MODEL_TYPE_LABELS, MODEL_TYPE_EMBEDDING, MODEL_TYPE_RERANK, MODEL_TYPES

#: 分类型的参数白名单：键必须能被对应的消费方真正用上，「填了不生效」比不填更难查
ALLOWED_CONFIG_KEYS_BY_TYPE: dict[str, frozenset[str]] = {
    # chat：init_chat_model 透传给 ChatOpenAI 的采样与传输参数（沿用 agents 层的白名单）
    MODEL_TYPE_CHAT: ALLOWED_CONFIG_KEYS,
    # embedding：EmbeddingClient 的构造参数（分批与重试）；连接类仍走 base_url / api_key 列
    MODEL_TYPE_EMBEDDING: frozenset({"timeout", "batch_size", "concurrency", "max_retries"}),
    # rerank：只消费 timeout——top_n 由检索链路按场景决定，不该被某个模型行改掉
    MODEL_TYPE_RERANK: frozenset({"timeout"}),
}

#: 新建时的默认参数模板（按类型）：页面会预填同一份，服务端在这里兜底，避免库里出现空 ``{}``
DEFAULT_CONFIG_BY_TYPE: dict[str, dict[str, Any]] = {
    MODEL_TYPE_CHAT: {"temperature": 0.2, "max_tokens": 1500, "timeout": 60, "max_retries": 2},
    MODEL_TYPE_EMBEDDING: {"timeout": 60, "batch_size": 16, "concurrency": 3, "max_retries": 2},
    MODEL_TYPE_RERANK: {"timeout": 30},
}


def default_config_for(model_type: str) -> dict[str, Any]:
    """该类型的默认参数（未知类型回落为空：类型本身已由校验器挡住）。"""
    return dict(DEFAULT_CONFIG_BY_TYPE.get(model_type, {}))


def check_config_keys(value: dict[str, Any] | None, model_type: str) -> dict[str, Any] | None:
    """保存前拦掉不该出现在该类型里的键：写进表里再被读取侧静默丢弃，是排查成本最高的一种失误。"""
    if not value:
        return value
    allowed = ALLOWED_CONFIG_KEYS_BY_TYPE.get(model_type, ALLOWED_CONFIG_KEYS)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"{MODEL_TYPE_LABELS.get(model_type, model_type)}的 default_config_json 含不支持的键："
            f"{', '.join(unknown)}；可用键：{', '.join(sorted(allowed))}。"
            f"连接类参数请用 base_url / api_key，流式开关由代码固定"
        )
    return value


class SystemModelConfigCreateSchema(BaseModel):
    """新增模型配置。"""

    model_type: str = Field(default=MODEL_TYPE_CHAT, description="模型类型：chat / embedding / rerank")
    model_config_name: str = Field(..., max_length=128, description="展示名称")
    model_config_key: str = Field(..., min_length=1, max_length=128, description="模型标识（全局唯一，业务配置引用它）")
    model_name: str = Field(default="", max_length=256, description="传给厂商的模型名；留空用模型标识")
    model_provider: str = Field(default="openai", max_length=32, description="供应商/协议（本地部署填 local 或 ollama）")
    base_url: str = Field(default="", max_length=512, description="网关地址；留空沿用同类 env")
    api_key: str = Field(default="", max_length=1024, description="模型密钥；留空沿用同类 env")
    default_config_json: dict[str, Any] = Field(
        default_factory=dict, description="按类型的默认参数（白名单见 ALLOWED_CONFIG_KEYS_BY_TYPE）"
    )
    description: str = Field(default="", max_length=512, description="模型描述")
    enabled: bool = Field(default=True, description="是否启用")
    is_default: bool = Field(default=False, description="是否该类型的默认模型")
    sort: int = Field(default=0, description="排序号，小的在前")

    @field_validator("model_type")
    @classmethod
    def check_model_type(cls, value: str) -> str:
        """类型只认三个值：拼错一个字母就会让整行配置在读取侧被忽略。"""
        normalized = value.strip().lower()
        if normalized not in MODEL_TYPES:
            raise ValueError(f"不支持的模型类型：{value}（可选：{'/'.join(MODEL_TYPES)}）")
        return normalized

    @field_validator("model_config_key", "model_name", "model_provider", "base_url", "api_key", "model_config_name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """两端空白一律清掉：粘贴地址时常带换行，留着会让 httpx 报难以定位的错。"""
        return value.strip()

    @model_validator(mode="after")
    def check_config_keys_by_type(self) -> "SystemModelConfigCreateSchema":
        check_config_keys(self.default_config_json, self.model_type)
        return self


class SystemModelConfigUpdateSchema(BaseModel):
    """更新模型配置（只动传入的字段；``api_key`` 传空串表示沿用原值）。

    ``default_config_json`` 的白名单校验不在这里做：更新可以不传 ``model_type``，
    此时得用库里那一行的类型做判定，而 schema 看不到库里——由服务层合并后校验。
    """

    model_type: str | None = Field(default=None, description="模型类型：chat / embedding / rerank")
    model_config_name: str | None = Field(default=None, max_length=128, description="展示名称")
    model_config_key: str | None = Field(default=None, min_length=1, max_length=128, description="模型标识（全局唯一）")
    model_name: str | None = Field(default=None, max_length=256, description="传给厂商的模型名")
    model_provider: str | None = Field(default=None, max_length=32, description="供应商/协议")
    base_url: str | None = Field(default=None, max_length=512, description="网关地址")
    api_key: str | None = Field(default=None, max_length=1024, description="模型密钥；空串=不修改")
    default_config_json: dict[str, Any] | None = Field(default=None, description="按类型的默认参数")
    description: str | None = Field(default=None, max_length=512, description="模型描述")
    enabled: bool | None = Field(default=None, description="是否启用")
    is_default: bool | None = Field(default=None, description="是否该类型的默认模型")
    sort: int | None = Field(default=None, description="排序号")

    @field_validator("model_type")
    @classmethod
    def check_model_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in MODEL_TYPES:
            raise ValueError(f"不支持的模型类型：{value}（可选：{'/'.join(MODEL_TYPES)}）")
        return normalized

    @field_validator("model_config_key", "model_name", "model_provider", "base_url", "api_key", "model_config_name", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class SystemModelConfigOutSchema(BaseSchema):
    """列表/详情出参：不含明文密钥。"""

    model_type: str = Field(default=MODEL_TYPE_CHAT, description="模型类型：chat / embedding / rerank")
    model_config_name: str = Field(..., description="展示名称")
    model_config_key: str = Field(..., description="模型标识")
    model_name: str = Field(default="", description="传给厂商的模型名")
    model_provider: str = Field(default="openai", description="供应商/协议")
    base_url: str = Field(default="", description="网关地址")
    api_key_masked: str = Field(default="", description="密钥掩码（完整密钥不出接口）")
    has_api_key: bool = Field(default=False, description="是否已配置独立密钥")
    default_config_json: dict[str, Any] = Field(default_factory=dict, description="按类型的默认参数")
    description: str = Field(default="", description="模型描述")
    enabled: bool = Field(default=True, description="是否启用")
    is_default: bool = Field(default=False, description="是否该类型的默认模型")
    sort: int = Field(default=0, description="排序号")


class SystemModelConfigQueryParam(BaseQueryParam):
    """列表筛选参数（``q`` 决定 CRUD 层的比较方式）。"""

    model_type: str | None = Field(default=None, max_length=16, description="模型类型", json_schema_extra={"q": "eq"})
    model_config_name: str | None = Field(
        default=None, max_length=128, description="展示名称", json_schema_extra={"q": "like"}
    )
    model_config_key: str | None = Field(
        default=None, max_length=128, description="模型标识", json_schema_extra={"q": "like"}
    )
    model_name: str | None = Field(default=None, max_length=256, description="模型名称", json_schema_extra={"q": "like"})
    model_provider: str | None = Field(default=None, max_length=32, description="供应商/协议", json_schema_extra={"q": "eq"})
    enabled: bool | None = Field(default=None, description="是否启用", json_schema_extra={"q": "eq"})


class SystemModelConfigTestOutSchema(BaseModel):
    """连通性测试结果：页面直接展示，不需要再去翻日志。"""

    ok: bool = Field(..., description="是否连通")
    model: str = Field(default="", description="本次测试实际使用的模型标识/模型名")
    latency_ms: int = Field(default=0, description="从发起到拿回首个完整响应的耗时（毫秒）")
    reply: str = Field(default="", description="模型回复（截断，仅用于确认它能正常说话）")
    error: str = Field(default="", description="失败原因（可读文案，不含密钥）")
