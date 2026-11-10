"""AI 模型配置表模型（表 ``sbp_ai_system_model_config``）。

一张表管三类模型：**对话**（chat：问答生成 / 证据二判 / FAQ 抽取）、**嵌入**（embedding：
分片与 query 向量）、**重排序**（rerank：召回后精排）。类型列不只是给列表分类好看——三类的
连接协议、可调参数与默认值本就不同（chat 是 temperature / max_tokens，embedding 是
batch_size / concurrency，rerank 只有 timeout），没有类型列时只能靠「参数键猜用途」，
也无法让某一类单独指定默认行。

为什么这张表归 system 域而不是 kb 域：它是**基座能力**——问答生成、证据二判、FAQ 抽取、
向量化、精排、页面维护六处共用同一份配置，任何一处私有化都会让「换个模型」变成改六份代码。
表名同样用 ``sbp_ai_system_`` 前缀（与 api_log / nonce / prompt_template 等同域），
不看前缀猜归属：前缀即域，代码目录、页面菜单与启动期建表登记三者一致。

本地部署：``model_provider`` 只决定「用哪种协议说话」，地址由 ``base_url`` 给——本地
vLLM / llama.cpp / TEI / Ollama 填内网地址即可，密钥可以留空（私有化服务通常不校验）；
provider 填 ``local`` / ``vllm`` 等 OpenAI 兼容写法时会在构造客户端时归一成 ``openai``。

与已废弃的 GSI 侧 ``sbp_km_model_config`` 的区别：那张表是 GSI Flyway 维护的只读字典，
AI 侧不能改结构也不能写；本表由 AI 侧自建自维护。

删除语义：物理删除（基座 ``ModelMixin`` 不提供软删除）。停用一个模型用 ``enabled=false``，
删掉之后历史指标里的 ``model`` 字段仍保留当时的 ``model_config_key`` 快照，不会指向空记录。
"""

from typing import Any

from sqlalchemy import Boolean, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin

#: 掩码保留的首尾字符数：足够运维分辨「是不是同一把 key」，又不至于被录屏带走
_MASK_KEEP = 4

#: 模型类型取值域：``default_config_json`` 的白名单与默认值都跟着类型走（见 ``schema.py``）
MODEL_TYPE_CHAT = "chat"
MODEL_TYPE_EMBEDDING = "embedding"
MODEL_TYPE_RERANK = "rerank"
MODEL_TYPES: tuple[str, ...] = (MODEL_TYPE_CHAT, MODEL_TYPE_EMBEDDING, MODEL_TYPE_RERANK)

#: 类型中文名：日志、错误提示与页面标签共用一套叫法
MODEL_TYPE_LABELS: dict[str, str] = {
    MODEL_TYPE_CHAT: "对话模型",
    MODEL_TYPE_EMBEDDING: "嵌入模型",
    MODEL_TYPE_RERANK: "重排序模型",
}


class SystemModelConfigModel(ModelMixin):
    """AI 模型配置（类型 + 展示名 + 调用标识 + 连接方式 + 默认调优参数）。"""

    __tablename__: str = "sbp_ai_system_model_config"
    __table_args__: tuple = (
        # 模型标识是业务引用键（LLM_MODEL_* 槽位、调用方入参都指向它），必须全局唯一；
        # 用唯一索引而不是 CHECK：并发建两行同名要由数据库挡住，服务层校验只是友好提示
        Index("uq_sbp_ai_system_model_config_model_config_key", "model_config_key", unique=True),
        # 列表默认按类型筛：embedding / rerank 各自只有一两行，但类型一定进 WHERE
        Index("ix_sbp_ai_system_model_config_model_type", "model_type"),
        Index("ix_sbp_ai_system_model_config_enabled", "enabled"),
        {"comment": "AI 模型配置表（基座能力：对话 / 嵌入 / 重排序三类的连接方式与默认参数）"},
    )

    model_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=MODEL_TYPE_CHAT,
        server_default=text("'chat'"),
        comment="模型类型：chat（对话）/ embedding（嵌入）/ rerank（重排序）",
    )
    model_config_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="展示名称（页面可选中的名字）"
    )
    model_config_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="模型标识：系统内的调用键（业务配置引用它，不引用记录 id）",
    )
    model_name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        default="",
        comment="模型名称：真正传给厂商的 model 参数；留空则用 model_config_key",
    )
    model_provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="openai",
        server_default=text("'openai'"),
        comment=(
            "供应商/协议：chat 对应 init_chat_model 的 model_provider（OpenAI 兼容网关填 openai；"
            "本地 vLLM / TEI 填 local 或 vllm，构造时归一成 openai；Ollama 填 ollama）；"
            "embedding 对应协议（非 ollama 一律 OpenAI 兼容）；rerank 仅作标识"
        ),
    )
    base_url: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
        comment="网关地址；留空沿用同类 env（LLM_BASE_URL / EMBEDDING_BASE_URL / RERANK_BASE_URL）",
    )
    api_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        default="",
        comment="模型密钥；留空沿用同类 env。明文入库，建议只放低权限网关 key",
    )
    default_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "按类型的默认参数（chat: temperature/max_tokens…；embedding: batch_size/concurrency…；"
            "rerank: timeout）：只是默认值，场景可在代码里逐键覆盖"
        ),
    )
    description: Mapped[str] = mapped_column(
        String(512), nullable=False, default="", comment="模型描述：适用场景、上下文长度、成本档次等"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        # 索引在上面的 __table_args__ 里显式声明：这里再写 index=True 会多建一条同义索引（规范 D12）
        comment="是否启用：停用后不再参与取配置（记录保留）",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否该类型的默认模型：同一类型只允许一行为 true，由服务层保证",
    )
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="排序号：页面列表升序，小的在前"
    )

    @property
    def api_key_masked(self) -> str:
        """掩码后的密钥：列表/详情只回显它，完整密钥不出接口。

        编辑时该字段留空表示「沿用原值」——见 ``SystemModelConfigService.update``。
        """
        if not self.api_key:
            return ""
        if len(self.api_key) <= _MASK_KEEP * 2:
            return "*" * len(self.api_key)
        return f"{self.api_key[:_MASK_KEEP]}****{self.api_key[-_MASK_KEEP:]}"

    @property
    def has_api_key(self) -> bool:
        """是否已配置密钥：页面据此显示「沿用 env」还是「独立密钥」。"""
        return bool(self.api_key)
