"""模型配置管理端（系统管理 → 模型配置）：路由登记 + 服务层规则的回归测试。

守住六条（都是「页面能做错、但代码必须拦住」的规则）：

1. **密钥不出接口**：出参只有掩码，短密钥整串打码；
2. **留空 = 不改**：更新时 ``api_key`` 传空串不能覆盖库里的原值（否则运维每改一次配置就把密钥存成空）；
3. **默认唯一（按类型）**：设默认只清同类型的标记，停用默认行要顺手清标记，默认行不许删；
4. **改完即生效**：所有写操作都要让配置快照过期，并重建嵌入 / 重排序客户端；
5. **连通性测试不抛 5xx**：失败是结果，要返回可读原因（页面要的是「为什么不通」）；
6. **按类型分流**：参数白名单、默认参数模板与探测方式都跟着 ``model_type`` 走。

不连数据库、不连模型：CRUD 与 LLM / 嵌入 / 重排序客户端都是桩。
"""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app import create_app
from app.core.exceptions import CustomException
from app.modules.system.model_config import service as service_module
from app.modules.system.model_config.model import SystemModelConfigModel
from app.modules.system.model_config.schema import (
    SystemModelConfigCreateSchema,
    SystemModelConfigUpdateSchema,
)
from app.modules.system.model_config.service import SystemModelConfigService

MODEL_CONFIG_PATHS: dict[str, set[str]] = {
    "/api/v1/system/model-config/list": {"get"},
    "/api/v1/system/model-config/detail/{id}": {"get"},
    "/api/v1/system/model-config/create": {"post"},
    "/api/v1/system/model-config/update/{id}": {"put"},
    "/api/v1/system/model-config/delete": {"delete"},
    "/api/v1/system/model-config/test/{id}": {"post"},
}


def _model(**overrides: Any) -> SystemModelConfigModel:
    """一行配置（不落库）：字段全给值，避免直接实例化时取不到 ORM 默认值。"""
    row = SystemModelConfigModel(
        model_type="chat",
        model_config_name="问答模型",
        model_config_key="kb-answer",
        model_name="",
        model_provider="openai",
        base_url="",
        api_key="sk-abcdefghijkl",
        default_config_json={},
        description="",
        enabled=True,
        is_default=False,
        sort=0,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class _FakeCRUD:
    """替身 CRUD：只实现 service 用到的几个方法，并记录调用参数。"""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.rows: dict[int, SystemModelConfigModel] = {1: _model()}
        self.created: list[SystemModelConfigCreateSchema] = []
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.deleted: list[list[int]] = []
        #: clear_other_defaults 的调用记录：(类型, 保留的 id)
        self.cleared: list[tuple[str, int | None]] = []
        #: 快照过期被调用的次数（写操作后必须 > 0）
        self.invalidations: list[int] = []
        #: 连接类客户端重建的次数（改了地址 / 密钥后必须 > 0）
        self.resets: list[int] = []
        #: 非空表示「库里已有同名 model_config_key」：用来验证唯一性校验
        self.conflict: SystemModelConfigModel | None = None

    async def get_or_404(self, *, id: int, msg: str = "", out_schema: Any = None) -> Any:
        row = self.rows.get(id)
        if row is None:
            raise CustomException(msg=msg)
        return out_schema.model_validate(row) if out_schema else row

    async def create(self, data: SystemModelConfigCreateSchema) -> SystemModelConfigModel:
        self.created.append(data)
        row = _model(**data.model_dump())
        row.id = max(self.rows) + 1
        self.rows[row.id] = row
        return row

    async def update(self, id: int, payload: dict[str, Any]) -> SystemModelConfigModel:
        self.updates.append((id, dict(payload)))
        row = self.rows[id]
        for key, value in payload.items():
            setattr(row, key, value)
        return row

    async def delete(self, ids: list[int]) -> None:
        self.deleted.append(list(ids))

    async def get_list(self, **kwargs: Any) -> list[SystemModelConfigModel]:
        return list(self.rows.values())

    async def get_by_config_key(self, model_config_key: str, *, exclude_id: int | None = None) -> Any:
        if self.conflict is not None and self.conflict.model_config_key == model_config_key:
            return self.conflict
        return None

    async def clear_other_defaults(self, model_type: str, *, keep_id: int | None = None) -> None:
        self.cleared.append((model_type, keep_id))


class _FakeLLM:
    """替身聊天客户端：成功返回固定文本，失败抛指定异常。"""

    def __init__(self, *, reply: str = "ok", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[Any] = []

    async def chat(self, messages: Any, *, profile: Any, overrides: Any = None) -> str:
        self.calls.append(SimpleNamespace(messages=messages, profile=profile, overrides=overrides))
        if self.error is not None:
            raise self.error
        return self.reply


class _FakeEmbeddingClient:
    """替身嵌入客户端：只验证「按行配置构造并要一条向量」。"""

    last: Any = None

    def __init__(self, base_url: str | None = None, **kwargs: Any) -> None:
        self.base_url = base_url
        self.kwargs = kwargs
        self.closed = False
        _FakeEmbeddingClient.last = self

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.texts = list(texts)
        return [[0.1, 0.2, 0.3]]

    async def aclose(self) -> None:
        self.closed = True


class _FakeRerankClient:
    """替身重排序客户端：返回一条候选结果，验证分数被写进可读文案。"""

    last: Any = None

    def __init__(self, base_url: str | None = None, **kwargs: Any) -> None:
        self.base_url = base_url
        self.kwargs = kwargs
        _FakeRerankClient.last = self

    async def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[Any]:
        self.query = query
        self.documents = list(documents)
        return [SimpleNamespace(score=0.875)]


@pytest.fixture
def fake_crud(monkeypatch: pytest.MonkeyPatch) -> _FakeCRUD:
    """把 CRUD 换成桩，并记录「快照过期」被调用了几次。"""
    fake = _FakeCRUD(db=object())
    monkeypatch.setattr(service_module, "SystemModelConfigCRUD", lambda db: fake)
    monkeypatch.setattr(service_module, "invalidate_model_config_cache", lambda: fake.invalidations.append(1))

    async def fake_reset() -> None:
        fake.resets.append(1)

    # 写库后要重建嵌入 / 重排序客户端：测试里不碰真实单例，只记次数
    monkeypatch.setattr(service_module, "reset_model_clients", fake_reset)
    return fake


def _create_payload(**overrides: Any) -> SystemModelConfigCreateSchema:
    data = {"model_config_name": "问答模型", "model_config_key": "kb-answer"}
    data.update(overrides)
    return SystemModelConfigCreateSchema(**data)


def test_routes_are_registered() -> None:
    """六个接口都挂在 /api/v1/system/model-config 下（含启停/设默认/测试）。"""
    paths = create_app().openapi()["paths"]
    for path, methods in MODEL_CONFIG_PATHS.items():
        assert path in paths, f"缺少路由：{path}"
        assert methods <= set(paths[path]), f"{path} 的方法不全：{paths[path]}"


def test_table_is_registered_for_bootstrap() -> None:
    """建表清单必须包含本表：漏登记时启动期不建表，且没有任何报错（只会在首次访问时报错 1146）。"""
    from app.modules.system.bootstrap import TABLES

    assert SystemModelConfigModel.__table__ in TABLES


def test_api_key_is_masked_and_short_keys_are_hidden() -> None:
    """出参只能拿到掩码；短到无法保留首尾时整串打码。"""
    assert _model().api_key_masked == "sk-a****ijkl"
    assert _model().has_api_key is True
    assert _model(api_key="short").api_key_masked == "*****"
    assert _model(api_key="").api_key_masked == ""
    assert _model(api_key="").has_api_key is False


def test_schema_rejects_connection_keys_in_config_json() -> None:
    """连接类参数不许进 ``default_config_json``：写进去会被读取侧丢弃，排查成本最高。"""
    with pytest.raises(ValidationError) as excinfo:
        _create_payload(default_config_json={"streaming": False, "base_url": "http://evil.local/v1"})

    message = str(excinfo.value)
    assert "base_url" in message
    assert "streaming" in message


async def test_create_rejects_duplicate_model_key(fake_crud: _FakeCRUD) -> None:
    """模型标识唯一：业务配置按它取档案，重名等于随机挑一行。"""
    fake_crud.conflict = _model(model_config_key="kb-answer")

    with pytest.raises(CustomException) as excinfo:
        await SystemModelConfigService(db=object()).create(data=_create_payload())

    assert "模型标识已存在" in excinfo.value.msg
    assert fake_crud.created == []


async def test_create_rejects_default_on_disabled_model(fake_crud: _FakeCRUD) -> None:
    """停用的行不能当默认：否则槽位全空时会取到一个永不被使用的模型。"""
    with pytest.raises(CustomException) as excinfo:
        await SystemModelConfigService(db=object()).create(data=_create_payload(is_default=True, enabled=False))

    assert "停用的模型不能设为默认模型" in excinfo.value.msg


async def test_create_default_clears_others_and_invalidates_snapshot(fake_crud: _FakeCRUD) -> None:
    """设默认要清掉别人的默认标记，并让进程内快照过期（页面点完保存就生效）。"""
    result = await SystemModelConfigService(db=object()).create(
        data=_create_payload(is_default=True, api_key="sk-abcdefghijkl")
    )

    assert fake_crud.cleared == [("chat", None)], "新增默认行只清同类型，且不留 keep_id"
    assert fake_crud.invalidations == [1]
    assert fake_crud.resets == [1], "新增后要重建连接类客户端"
    assert result.is_default is True
    assert result.api_key_masked == "sk-a****ijkl"


async def test_update_keeps_api_key_when_blank(fake_crud: _FakeCRUD) -> None:
    """密钥留空 = 沿用原值：payload 里不能出现 api_key 字段。"""
    await SystemModelConfigService(db=object()).update(
        id=1,
        data=SystemModelConfigUpdateSchema(model_config_name="改名", api_key=""),
    )

    _, payload = fake_crud.updates[0]
    assert "api_key" not in payload
    assert payload["model_config_name"] == "改名"
    assert fake_crud.invalidations == [1]


async def test_update_replaces_api_key_when_provided(fake_crud: _FakeCRUD) -> None:
    """粘贴了新密钥就覆盖（页面只回显掩码，不可能「顺手保存」把掩码写回库）。"""
    await SystemModelConfigService(db=object()).update(
        id=1,
        data=SystemModelConfigUpdateSchema(api_key="sk-new-key-0001"),
    )

    _, payload = fake_crud.updates[0]
    assert payload["api_key"] == "sk-new-key-0001"


async def test_update_disabling_default_row_clears_the_flag(fake_crud: _FakeCRUD) -> None:
    """停用默认行要顺手清掉默认标记，避免「停用了却还是默认」的矛盾状态。"""
    fake_crud.rows[1] = _model(is_default=True)

    result = await SystemModelConfigService(db=object()).update(
        id=1,
        data=SystemModelConfigUpdateSchema(enabled=False),
    )

    assert result.enabled is False
    assert result.is_default is False


async def test_update_rejects_default_on_disabled_model(fake_crud: _FakeCRUD) -> None:
    """把已停用的行设为默认同样要拦下来。"""
    fake_crud.rows[1] = _model(enabled=False)

    with pytest.raises(CustomException) as excinfo:
        await SystemModelConfigService(db=object()).update(
            id=1,
            data=SystemModelConfigUpdateSchema(is_default=True),
        )

    assert "停用的模型不能设为默认模型" in excinfo.value.msg


async def test_delete_rejects_default_row(fake_crud: _FakeCRUD) -> None:
    """默认模型删掉之后槽位全空就只剩 env 兜底，必须先把默认切走。"""
    fake_crud.rows[1] = _model(is_default=True)

    with pytest.raises(CustomException) as excinfo:
        await SystemModelConfigService(db=object()).delete(ids=[1])

    assert "默认模型不能删除" in excinfo.value.msg
    assert fake_crud.deleted == []


async def test_delete_rejects_empty_ids(fake_crud: _FakeCRUD) -> None:
    """空列表不当作「批量删除全部」。"""
    with pytest.raises(CustomException) as excinfo:
        await SystemModelConfigService(db=object()).delete(ids=[])

    assert "不能为空" in excinfo.value.msg


async def test_delete_invalidates_snapshot(fake_crud: _FakeCRUD) -> None:
    """正常删除也要让快照过期，否则删掉的模型还能被问答取到。"""
    await SystemModelConfigService(db=object()).delete(ids=[1])

    assert fake_crud.deleted == [[1]]
    assert fake_crud.invalidations == [1]


async def test_connection_success_reports_latency_and_reply(fake_crud: _FakeCRUD, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试通：返回 ok + 耗时 + 截断后的回复，并把最小参数与测试提示词传下去。"""
    llm = _FakeLLM(reply="  ok  ")
    monkeypatch.setattr(service_module, "get_llm_client", lambda: llm)

    result = await SystemModelConfigService(db=object()).test_connection(id=1)

    assert result.ok is True
    assert result.reply == "ok"
    assert result.error == ""
    assert result.latency_ms >= 0
    assert result.model == "kb-answer"
    call = llm.calls[0]
    assert call.overrides == {"temperature": 0.0, "max_tokens": 32}
    assert call.profile.key == "kb-answer"


async def test_connection_failure_is_reported_not_raised(fake_crud: _FakeCRUD, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不通是结果而不是服务故障：返回可读原因，让页面能直接显示。"""
    monkeypatch.setattr(service_module, "get_llm_client", lambda: _FakeLLM(error=RuntimeError("connect timeout")))

    result = await SystemModelConfigService(db=object()).test_connection(id=1)

    assert result.ok is False
    assert result.reply == ""
    assert "RuntimeError" in result.error
    assert "connect timeout" in result.error


async def test_connection_times_out_with_readable_message(fake_crud: _FakeCRUD, monkeypatch: pytest.MonkeyPatch) -> None:
    """超时单独给一句人能读懂的文案（不把 TimeoutError 原样抛给页面）。"""
    monkeypatch.setattr(service_module, "get_llm_client", lambda: _FakeLLM(error=TimeoutError()))

    result = await SystemModelConfigService(db=object()).test_connection(id=1)

    assert result.ok is False
    assert "超时" in result.error


async def test_detail_hides_plain_api_key(fake_crud: _FakeCRUD) -> None:
    """详情出参同样不含明文密钥：页面能看到的只有掩码与「是否已配置」。"""
    detail = await SystemModelConfigService(db=object()).detail(id=1)

    assert detail.api_key_masked == "sk-a****ijkl"
    assert detail.has_api_key is True
    assert "api_key" not in detail.model_dump()


async def test_create_fills_default_config_for_the_type(fake_crud: _FakeCRUD) -> None:
    """留空就给该类型的推荐默认值：库里出现空 ``{}`` 时，页面看不到任何可调项。"""
    await SystemModelConfigService(db=object()).create(
        data=_create_payload(model_type="embedding", model_config_key="bge-m3"),
    )

    assert fake_crud.created[0].default_config_json == {
        "timeout": 60,
        "batch_size": 16,
        "concurrency": 3,
        "max_retries": 2,
    }


async def test_embedding_row_rejects_chat_only_keys(fake_crud: _FakeCRUD) -> None:
    """参数白名单跟类型走：嵌入行填 temperature 当场拦下，而不是写进表再被静默丢弃。"""
    with pytest.raises(ValidationError) as excinfo:
        _create_payload(model_type="embedding", default_config_json={"temperature": 0.3})

    assert "嵌入模型" in str(excinfo.value)
    assert fake_crud.created == []


async def test_create_default_does_not_touch_other_types(fake_crud: _FakeCRUD) -> None:
    """嵌入行设为默认时清的是 embedding 的默认，不能把对话行的默认顺手清掉。"""
    await SystemModelConfigService(db=object()).create(
        data=_create_payload(model_type="embedding", model_config_key="bge-m3", is_default=True),
    )

    assert fake_crud.cleared == [("embedding", None)]


async def test_update_switching_type_clears_default_in_new_type(fake_crud: _FakeCRUD) -> None:
    """把默认行改成另一类：默认标记落到新类型上，必须先清掉新类型原有的默认行。"""
    fake_crud.rows[1] = _model(is_default=True)

    await SystemModelConfigService(db=object()).update(
        id=1,
        data=SystemModelConfigUpdateSchema(model_type="embedding", default_config_json={"timeout": 30}),
    )

    assert fake_crud.cleared == [("embedding", 1)]


async def test_update_rejects_config_key_not_allowed_for_type(fake_crud: _FakeCRUD) -> None:
    """更新不传类型时用库里的类型校验：重排序只认 timeout。"""
    fake_crud.rows[1] = _model(model_type="rerank", model_config_key="bge-reranker")

    with pytest.raises(CustomException) as excinfo:
        await SystemModelConfigService(db=object()).update(
            id=1,
            data=SystemModelConfigUpdateSchema(default_config_json={"max_tokens": 100}),
        )

    assert "重排序模型" in excinfo.value.msg


async def test_embedding_probe_reports_vector_dim(fake_crud: _FakeCRUD, monkeypatch: pytest.MonkeyPatch) -> None:
    """嵌入模型测试：按行配置建临时客户端、算一条，回报维度（维度不对能当场看出来）。"""
    fake_crud.rows[1] = _model(
        model_type="embedding",
        model_config_name="嵌入模型",
        model_config_key="bge-m3",
        model_name="BAAI/bge-m3",
        model_provider="openai",
        base_url="http://embed.local/v1",
        api_key="",
        default_config_json={"timeout": 30, "batch_size": 16},
    )
    monkeypatch.setattr(service_module, "EmbeddingClient", _FakeEmbeddingClient)

    result = await SystemModelConfigService(db=object()).test_connection(id=1)

    assert result.ok is True
    assert result.reply == "向量维度 3"
    assert result.model == "BAAI/bge-m3"
    client = _FakeEmbeddingClient.last
    assert client.base_url == "http://embed.local/v1"
    assert client.kwargs["provider"] == "openai", "非 ollama 一律走 OpenAI 兼容（含本地 vLLM / TEI）"
    assert client.kwargs["api_key"] is None, "密钥留空时不能把空串传给客户端"
    assert (client.kwargs["batch_size"], client.kwargs["max_retries"]) == (1, 0), "测试只算一条，不重试"
    assert client.kwargs["timeout"] == 30.0
    assert client.closed is True, "探测用的临时客户端必须关掉"


async def test_rerank_probe_reports_top_score(fake_crud: _FakeCRUD, monkeypatch: pytest.MonkeyPatch) -> None:
    """重排序模型测试：两个候选排一次，回报 top1 分数（报文结构不对会立即失败）。"""
    fake_crud.rows[1] = _model(
        model_type="rerank",
        model_config_name="重排序模型",
        model_config_key="bge-reranker",
        model_name="BAAI/bge-reranker-v2-m3",
        base_url="http://rerank.local/v1",
        api_key="",
        default_config_json={"timeout": 30},
    )
    monkeypatch.setattr(service_module, "RerankClient", _FakeRerankClient)

    result = await SystemModelConfigService(db=object()).test_connection(id=1)

    assert result.ok is True
    assert result.reply == "返回 1 条结果，top1 相关度 0.8750"
    assert _FakeRerankClient.last.kwargs["timeout"] == 30.0
