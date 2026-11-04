"""骨架期通用请求体：公共字段可选，其余字段原样透传。

字段必填性随各 Tx 落地（手册逐接口定义），这里只负责「请求体必须是 JSON 对象」这一层校验。
``extra="allow"`` 是刻意为之：M1 冒烟只带 ``request_id`` / ``tenant_id`` / ``user_id``，
若在此强制更多字段，骨架接口会从 501 变成 400，破坏既有验收口径。
"""

from pydantic import BaseModel, ConfigDict, Field


class ContractRequest(BaseModel):
    """`/v1` 骨架接口的通用正文。"""

    model_config = ConfigDict(extra="allow")

    request_id: str | None = Field(default=None, description="请求标识；写操作同时作为幂等键")
    tenant_id: str | None = Field(default=None, description="租户标识，当前固定 `gsi-default`")
    user_id: str | None = Field(default=None, description="操作人标识，用于审计与权限下推")
