"""集成-AI 问答-会话与消息的落库入参（仅供服务层内部使用，不对外暴露）。"""

from pydantic import BaseModel, Field


class MessageCreateSchema(BaseModel):
    """消息落库模型（流式链路 user / assistant 共用）"""

    session_id: int = Field(..., ge=1, description="所属会话ID")
    role: int = Field(..., description="角色(1:用户 2:助手)")
    content: str = Field(..., min_length=1, description="正文")
    analysis: dict | None = Field(default=None, description="思考链路（仅助手）")
    cites: list[dict] | None = Field(default=None, description="引用快照（仅助手）")
    follow_ups: list[str] | None = Field(default=None, description="问题拓展（仅助手）")
    disclaimer: str | None = Field(default=None, description="免责声明文案")
    not_found: bool = Field(default=False, description="拒答标记")
    model_name: str | None = Field(default=None, description="生成模型")
    tokens_in: int = Field(default=0, ge=0, description="输入 Token")
    tokens_out: int = Field(default=0, ge=0, description="输出 Token")
    latency_ms: int = Field(default=0, ge=0, description="端到端耗时(毫秒)")
