"""重复治理契约 Schema（#16 保存前查重 / #17 分组查询 / #18 分组详情 / #19 合并预览 / D06 切片查重）。

字段与手册 D01–D06 逐条对齐（``#20`` 停用属于知识写入口，在 ``knowledge/schema.py``）。
两类校验刻意分开放：

1. **结构校验**（枚举、必填、``seq`` 唯一、草稿必须在清单内）放在这里：失败即 400
   ``INVALID_REQUEST``，调用方看到的是「参数不对」；
2. **容量校验**（``scope`` 超过 1000 项）**不**放这里：它必须回 413 ``PAYLOAD_TOO_LARGE``，
   写进 Pydantic 会被归一成 400，把「数据太多」误报成「参数不对」（契约明确要求 413）。

``kinds`` 留空按「五类全查」解释：把空数组当「不检测任何类型」会返回一份没有分组的成功结果，
正是 §九 禁止的「失败显示未发现重复」。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validator import ensure_non_blank
from app.modules.kb.dup_result.schema import (
    DEFAULT_GROUP_PAGE,
    DUP_KINDS,
    MAX_GROUP_PAGE,
    MERGE_ACTIONS,
    RESOURCE_TYPES,
    SCORE_PROFILE,
)

_ENTRY_MODES: tuple[str, ...] = ("ai", "manual")


def _check_enum(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} 只支持 {' / '.join(allowed)}")
    return value


class GovernanceResourceInput(BaseModel):
    """治理对象的不可变内容身份（``scope.resources[]`` / ``candidate.resource`` 同构）。"""

    model_config = ConfigDict(extra="ignore")

    resource_type: str = Field(description="知识类型：document / faq")
    resource_id: str = Field(description="GSI 文档或 FAQ 主键（不是页面 hit_id）")
    record_id: str = Field(description="已发布快照 ID 或 GSI 草稿冻结快照 ID")
    content_hash: str = Field(description="本次完整内容摘要，变更后必须重新检测")
    publication_ids: list[str] = Field(description="本内容对应的发布单元集合；草稿为 []")
    entry_mode: str = Field(description="录入来源：ai / manual，用于 FAQ 分类")

    @field_validator("resource_id", "record_id", "content_hash")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("resource_type")
    @classmethod
    def _resource_type_enum(cls, value: str) -> str:
        return _check_enum(value, RESOURCE_TYPES, "resource_type")

    @field_validator("entry_mode")
    @classmethod
    def _entry_mode_enum(cls, value: str) -> str:
        return _check_enum(value, _ENTRY_MODES, "entry_mode")

    @field_validator("publication_ids")
    @classmethod
    def _publication_ids_not_blank(cls, value: list[str]) -> list[str]:
        return [ensure_non_blank(item) for item in value]


class SpotChunkInput(BaseModel):
    """草稿/候选的一个切片；``chunk_id`` 为空表示尚未入库。"""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str | None = Field(default=None, description="AI 维护的正式切片 ID；草稿切片省略")
    seq: int = Field(ge=1, description="冻结草稿内从 1 开始的唯一顺序")
    title: str = Field(description="切片标题")
    content: str = Field(description="切片纯文本内容")

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_not_blank(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        return ensure_non_blank(value)


def _assert_unique_seq(chunks: list[SpotChunkInput], field: str) -> list[SpotChunkInput]:
    """``seq`` 是草稿切片的定位键：重复会让命中回不到唯一来源。"""
    seqs = [chunk.seq for chunk in chunks]
    if len(set(seqs)) != len(seqs):
        raise ValueError(f"{field}.chunks 内 seq 重复，同一次检测内必须唯一。")
    chunk_ids = [chunk.chunk_id for chunk in chunks if chunk.chunk_id is not None]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError(f"{field}.chunks 内 chunk_id 重复，同一次检测内必须唯一。")
    return chunks


class CandidateInput(BaseModel):
    """保存前的候选内容快照（只用于本次比较，不落正式索引）。"""

    model_config = ConfigDict(extra="ignore")

    resource: GovernanceResourceInput
    title: str = Field(description="保存前标题")
    summary: str = Field(default="", description="摘要，可空")
    content: str = Field(description="已清洗的纯文本正文；FAQ 为答案")
    chunks: list[SpotChunkInput] = Field(default_factory=list, description="草稿切片；未切片时 []")

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("chunks")
    @classmethod
    def _chunk_set_shape(cls, value: list[SpotChunkInput]) -> list[SpotChunkInput]:
        return _assert_unique_seq(value, "candidate")


class DraftInput(BaseModel):
    """``scope.drafts[]``：AI 尚无正式内容的 GSI 草稿，由调用方随请求提供。"""

    model_config = ConfigDict(extra="ignore")

    resource: GovernanceResourceInput
    title: str = Field(description="保存前标题")
    summary: str = Field(default="", description="摘要，可空")
    content: str = Field(description="已清洗的纯文本正文；FAQ 为答案")
    chunks: list[SpotChunkInput] = Field(default_factory=list, description="草稿切片；未切片时 []")

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("chunks")
    @classmethod
    def _chunk_set_shape(cls, value: list[SpotChunkInput]) -> list[SpotChunkInput]:
        return _assert_unique_seq(value, "scope.drafts")


class GovernanceScope(BaseModel):
    """按知识管理权限签发的治理范围（不是座席消费授权）。"""

    model_config = ConfigDict(extra="ignore")

    scope_id: str = Field(description="本次范围追踪 ID")
    issued_at: int = Field(ge=0, description="签发时间，Unix 毫秒")
    expires_at: int = Field(ge=0, description="到期时间，Unix 毫秒，跨度 ≤120s")
    resources: list[GovernanceResourceInput] = Field(description="授权治理内容清单；[] 表示无候选")
    drafts: list[DraftInput] = Field(description="AI 尚无正式内容的草稿/审批快照；无则 []")

    @field_validator("scope_id")
    @classmethod
    def _scope_id_not_blank(cls, value: str) -> str:
        return ensure_non_blank(value)

    @model_validator(mode="after")
    def _consistent_scope(self) -> "GovernanceScope":
        """三条一致性：清单不重复、草稿挂在清单身份上、草稿摘要与清单声明一致。

        任一条不成立都必须拒绝：清单里出现两份同一资源会让 ``coverage`` 永远对不上
        （``evaluated`` 只会按身份去重后计数），草稿摘要对不上则是拿旧草稿冒充新内容。
        """
        identities: dict[tuple[str, str], GovernanceResourceInput] = {}
        for resource in self.resources:
            key = (resource.resource_type, resource.resource_id)
            if key in identities:
                raise ValueError(f"scope.resources 内 {key[0]}:{key[1]} 重复，同一内容只能出现一次。")
            identities[key] = resource

        for draft in self.drafts:
            key = (draft.resource.resource_type, draft.resource.resource_id)
            declared = identities.get(key)
            if declared is None:
                raise ValueError(f"scope.drafts 内 {key[0]}:{key[1]} 不在 scope.resources 内，草稿必须挂在清单身份上。")
            if declared.record_id != draft.resource.record_id or declared.content_hash != draft.resource.content_hash:
                raise ValueError(
                    f"scope.drafts 内 {key[0]}:{key[1]} 的 record_id / content_hash 与 scope.resources 声明不一致。"
                )
        return self


class _ScopedRequest(BaseModel):
    """四个接口共用的请求头字段（幂等键、租户、操作人）。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    tenant_id: str
    user_id: str
    scope: GovernanceScope

    @field_validator("request_id", "tenant_id", "user_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)


class _KindedRequest(_ScopedRequest):
    """带检测策略的两类请求（D01 / D02 共用）。"""

    score_profile: str = Field(default=SCORE_PROFILE, description="检测策略标识，本期固定 gsi-duplicate-v1")
    kinds: list[str] = Field(default_factory=list, description="本次治理类型，留空按五类全查")

    @field_validator("kinds")
    @classmethod
    def _kinds_enum(cls, value: list[str]) -> list[str]:
        """去重保序并校验枚举；空数组按「全部类型」解释（见模块 docstring）。"""
        result: list[str] = []
        for kind in value:
            _check_enum(kind, DUP_KINDS, "kinds")
            if kind not in result:
                result.append(kind)
        return result or list(DUP_KINDS)


class DuplicateCheckRequest(_KindedRequest):
    """#16 保存前查重请求正文。"""

    candidate: CandidateInput


def _resource_signature(resource: GovernanceResourceInput) -> tuple[str, str, str, str, tuple[str, ...], str]:
    """资源的全字段签名：用于「候选必须与清单中一项完全一致」的比对。

    ``publication_ids`` 按排序后比较：同一内容的多个发布单元没有先后语义，
    顺序不同不该判 400，但多/少一个发布单元就是另一份身份，必须拒绝。
    """
    return (
        resource.resource_type,
        resource.resource_id,
        resource.record_id,
        resource.content_hash,
        tuple(sorted(resource.publication_ids)),
        resource.entry_mode,
    )


class ChunkCheckRequest(_ScopedRequest):
    """D06 单篇知识切片查重请求正文。"""

    candidate_resource: GovernanceResourceInput = Field(
        description="当前查重知识的完整快照身份，必须与 scope.resources 中一项完全一致"
    )

    @model_validator(mode="after")
    def _candidate_in_scope(self) -> "ChunkCheckRequest":
        """候选必须在范围内且逐字段一致。

        只对得上 ``resource_id`` 而 ``record_id`` / ``content_hash`` 不同，等于拿另一个版本的
        正文去和本范围比，算出来的「重复占比」不成立；而不在范围内则属于越权检测。
        """
        wanted = _resource_signature(self.candidate_resource)
        if not any(_resource_signature(resource) == wanted for resource in self.scope.resources):
            raise ValueError(
                "candidate_resource 必须与 scope.resources 中一项完全一致"
                "（resource_type / resource_id / record_id / content_hash / publication_ids / entry_mode 全部相同）。"
            )
        return self


class DuplicateGroupsQueryRequest(_KindedRequest):
    """#17 查询重复分组请求正文：首查带 ``scope``，翻页带 ``result_id`` + 新签发的同范围 ``scope``。"""

    result_id: str | None = Field(default=None, description="AI 返回的只读检测结果 ID；首次省略")
    cursor: str | None = Field(default=None, description="结果内分页游标；首次省略")
    limit: int = Field(default=DEFAULT_GROUP_PAGE, ge=1, le=MAX_GROUP_PAGE, description="每页分组数，1–100")

    @field_validator("result_id", "cursor")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return ensure_non_blank(value) if value is not None else None


class DuplicateGroupQueryRequest(_ScopedRequest):
    """#18 分组详情请求正文。"""

    result_id: str = Field(description="AI 返回的只读检测结果 ID")

    @field_validator("result_id")
    @classmethod
    def _result_id_not_blank(cls, value: str) -> str:
        return ensure_non_blank(value)


class MergeActionInput(BaseModel):
    """#19 单个成员的处置动作。"""

    model_config = ConfigDict(extra="ignore")

    target_hit_id: str = Field(description="被处置的成员 hit_id")
    action: str = Field(description="keep / drop / discard / merge_answer")
    merged_answer_html: str | None = Field(default=None, description="人工合并后的答案（仅 merge_answer）")

    @field_validator("target_hit_id")
    @classmethod
    def _target_not_blank(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("action")
    @classmethod
    def _action_enum(cls, value: str) -> str:
        return _check_enum(value, MERGE_ACTIONS, "actions[].action")


class MergePreviewRequest(_ScopedRequest):
    """#19 合并预览请求正文（不落正式库，只产出草稿态结果）。"""

    result_id: str = Field(description="AI 返回的只读检测结果 ID")
    group_id: str = Field(description="检测结果中的分组标识")
    keep_hit_id: str = Field(description="人工指定的主项 hit_id，必须属于该分组")
    actions: list[MergeActionInput] = Field(description="组内每个成员恰好一个处置动作")
    prompt_text: str = Field(default="", description="人工补充的合并说明，原样回显在预览里")
    model_key: str = Field(default="default", description="生成模型标识（本期不参与实际生成）")

    @field_validator("result_id", "group_id", "keep_hit_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return ensure_non_blank(value)

    @field_validator("actions")
    @classmethod
    def _actions_unique(cls, value: list[MergeActionInput]) -> list[MergeActionInput]:
        """同一成员出现两次动作时口径不明（后写的覆盖前一条？），直接拒绝。"""
        targets = [action.target_hit_id for action in value]
        if len(set(targets)) != len(targets):
            raise ValueError("actions 内 target_hit_id 重复，每个成员只能有一个处置动作。")
        return value
