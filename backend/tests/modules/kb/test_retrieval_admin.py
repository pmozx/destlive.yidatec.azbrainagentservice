"""知识检索管理端（/kb/retrieval）：通道配置视图 + 关键词/自然语言检索。

验收断言：
1. 配置视图回显当前检索参数，模型未接入时 ``*_ready`` 如实为 false（页面据此判断通道可用性）；
2. 关键词通道返回 chunk 级 hits，``authorized_publication_count`` 能把「一条都无权看」与
   「有权看但库里没命中」分开——这两种情况在契约里都是 ``no_evidence=true``；
3. 查询词只有空白 → 422，不放行到检索链路。

数据装置复用 ``search_support``（与 #07 契约用例同一套授权链摆法）；管理端不需要签名五头，
直接走进程内 ASGI 调用。
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.config.setting import settings
from app.modules.kb.retrieval.schema import KbRetrievalSearchSchema

from tests.modules.kb.search_support import TENANT, USER_ID, cleanup, entry, ready, team
from tests.support import call, requires_db


def _body(**overrides: Any) -> bytes:
    payload = {
        "query": "vpn",
        "mode": "keyword",
        "tenant_id": TENANT,
        "user_id": str(USER_ID),
        "limit": 5,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode()


def test_retrieval_search_schema_rejects_blank_query() -> None:
    """空白查询词在入参层就被挡住：没有关键词时授权与召回都没有意义。"""
    with pytest.raises(ValidationError):
        KbRetrievalSearchSchema(query="   ", user_id="1")


async def test_retrieval_config_reports_channel_params() -> None:
    """配置视图：回显运行时检索参数，模型是否接入按配置如实给出。"""
    response = await call("GET", "/api/v1/kb/retrieval/config")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["rrf_k"] == settings.SEARCH_RRF_K
    assert data["snippet_length"] == settings.SEARCH_SNIPPET_LENGTH
    assert data["vector_dim"] == settings.VECTOR_DIM
    assert 0 <= data["vector_min_score"] <= 1
    assert data["embedding_ready"] is bool(settings.EMBEDDING_BASE_URL)
    # 精排链路的四个闸门参数必须与实际生效值一致（页面只读展示，不能变成硬编码）
    assert data["rerank_window"] == settings.SEARCH_RERANK_WINDOW
    assert data["rerank_doc_chars"] == settings.SEARCH_RERANK_DOC_CHARS
    assert data["rerank_timeout"] == settings.SEARCH_RERANK_TIMEOUT
    assert data["rerank_relative_ratio"] == settings.KB_RERANK_RELATIVE_RATIO
    assert data["rerank_score_is_logit"] is settings.RERANK_SCORE_IS_LOGIT


@requires_db
async def test_retrieval_search_returns_hits_for_authorized_user() -> None:
    """授权范围内命中：hits 带契约字段，授权发布单元数如实回显。"""
    await ready()
    team_id, group_code = await team(members=[USER_ID])
    publication_id, _ = await entry(
        team=group_code,
        chunks=[{"seq": 1, "heading": "开通流程", "content": "在服务台提交 vpn 申请，由负责人开通账号。"}],
    )
    try:
        response = await call("POST", "/api/v1/kb/retrieval/search", body=_body(query="vpn"))
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["mode"] == "keyword"
        assert data["authorized_publication_count"] == 1
        assert data["returned_count"] == 1
        assert data["no_evidence"] is False
        assert data["elapsed_ms"] >= 0
        hit = data["hits"][0]
        assert hit["publication_id"] == publication_id
        assert hit["chunk_id"].startswith("record-")
        assert 0 <= hit["score"] <= 1
        assert "vpn" in hit["snippet"]
    finally:
        await cleanup([publication_id], [team_id])


@requires_db
async def test_retrieval_search_reports_empty_authorization() -> None:
    """用户不在任何受众团队：0 条命中且授权发布单元数为 0，页面据此提示「先配授权」。"""
    await ready()
    response = await call("POST", "/api/v1/kb/retrieval/search", body=_body(user_id="900999"))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["hits"] == []
    assert data["no_evidence"] is True
    assert data["authorized_publication_count"] == 0
