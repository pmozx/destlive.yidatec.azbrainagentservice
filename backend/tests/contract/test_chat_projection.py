"""会话与问答契约投影（#08–#15）：时间口径、身份换算、消息投影与台账回执。

这一层不碰数据库，锁的是「契约字段怎么出现」——真正容易写错、又最难在联调时看出来的地方：

- 时间必须是 ``YYYY-MM-DDTHH:MM:SSZ``：换成 ``+08:00`` 或带毫秒，同一个时刻两种写法就会
  让比对失败；
- 不可展示的消息只回 ID / 角色 / 时间 / 原因，正文与引用一个字都不能漏；
- 台账只存「回执除正文外的字段」（DR-08），答案由 #15 回查消息表补回。
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.modules.kb.chat.schema import REASON_SOURCE_UNAVAILABLE, HistoryMessage
from app.modules.kb.chat_message.schema import MessageRecord
from app.modules.system.request_log.service import (
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from app.modules_api.contract import errors as contract_errors
from app.modules_api.contract.chat import service as chat_service
from app.modules_api.contract.chat.schema import (
    COMPLETION_STATUS_COMPLETED,
    ChatBaseRequest,
    ChatCompletionResponse,
)
from app.utils.cursor import encode_cursor

_CITATION = {
    "index": 1,
    "citation_id": "c1",
    "resource_type": "document",
    "resource_id": "doc-1",
    "publication_id": "pub-1",
    "record_id": "record-1",
    "version_id": "1",
    "revision": 101,
    "chunk_id": "chunk-1",
    "quote": "普通申请 1 个工作日内完成审核。",
}
_SNAPSHOT = {
    "resource_type": "document",
    "resource_id": "doc-1",
    "publication_id": "pub-1",
    "record_id": "record-1",
    "version_id": "1",
    "revision": 101,
}
#: 落库的 ``knowledge_deps`` 只存依赖三元组（失效判定用，见 04-项目模块说明 §6.8）
_DEPENDENCY = {"publication_id": "pub-1", "record_id": "record-1", "revision": 101}


def _record(**overrides: object) -> MessageRecord:
    """一条助手消息的领域快照（用例只改需要的字段）。"""
    data: dict[str, object] = {
        "message_id": "assistant-1",
        "session_id": "session-1",
        "role": "assistant",
        "content": "普通申请 1 个工作日内完成审核。",
        "citations": [dict(_CITATION)],
        "knowledge_deps": [dict(_DEPENDENCY)],
        "status": "active",
        "created_time": datetime(2026, 11, 3, 8, 0, 0, tzinfo=UTC),
    }
    data.update(overrides)
    return MessageRecord(**data)


def _identity(user_id: str) -> ChatBaseRequest:
    return ChatBaseRequest(request_id="req-1", tenant_id="gsi-default", user_id=user_id)


def test_timestamps_are_utc_seconds_with_z() -> None:
    """同一时刻只有一种写法：UTC、秒级、带 Z；无时区按 UTC（库里存的就是 UTC）。"""
    moment = datetime(2026, 11, 3, 8, 0, 0, tzinfo=UTC)
    assert chat_service._iso(moment) == "2026-11-03T08:00:00Z"
    assert chat_service._iso(moment.astimezone(timezone(timedelta(hours=8)))) == "2026-11-03T08:00:00Z"
    assert chat_service._iso(datetime(2026, 11, 3, 8, 0, 0)) == "2026-11-03T08:00:00Z"
    assert chat_service._iso(moment.replace(microsecond=123_456)) == "2026-11-03T08:00:00Z"
    assert chat_service._iso(None) == ""


def test_user_id_must_be_a_numeric_primary_key() -> None:
    """契约 ``user_id`` 是数字主键的字符串形式：猜不出就 400，不拿它去下推权限。"""
    assert chat_service._user_pk(_identity(" 42 ")) == 42
    for value in ("user-demo", "0", "-7", "abc"):
        with pytest.raises(contract_errors.ContractApiError) as excinfo:
            chat_service._user_pk(_identity(value))
        assert excinfo.value.code == contract_errors.INVALID_REQUEST, value


def test_cursor_is_validated_before_reaching_domain_layer() -> None:
    """自造游标属于参数错误：领域层抛 ``ValueError``，漏出去会变成 500。"""
    assert chat_service._validate_cursor(None) is None
    assert chat_service._validate_cursor(encode_cursor(20)) is None
    with pytest.raises(contract_errors.ContractApiError) as excinfo:
        chat_service._validate_cursor("not-a-signed-cursor")
    assert excinfo.value.code == contract_errors.INVALID_REQUEST


def test_hidden_message_returns_only_identity_and_reason() -> None:
    """不可展示时正文、引用、来源一律不返回——半个来源清单比没有更容易误导。"""
    body = chat_service._history_message(HistoryMessage(record=_record(), content_visible=False))
    assert set(body) == {"message_id", "role", "content_visible", "created_at", "reason"}
    assert body["reason"] == REASON_SOURCE_UNAVAILABLE

    keep = chat_service._history_message(
        HistoryMessage(record=_record(), content_visible=False, reason=REASON_SOURCE_UNAVAILABLE)
    )
    assert keep["reason"] == REASON_SOURCE_UNAVAILABLE


def test_visible_assistant_message_carries_projected_evidence() -> None:
    """可展示时按手册字段清单投影：内部字段（title / score）不外泄，``no_evidence`` 派生。"""
    body = chat_service._history_message(HistoryMessage(record=_record(), content_visible=True))
    assert set(body) == {
        "message_id",
        "role",
        "content_visible",
        "created_at",
        "content",
        "citations",
        "source_snapshots",
        "no_evidence",
    }
    assert body["citations"] == [_CITATION]
    # 快照从引用投影：``knowledge_deps`` 缺 resource_type / version_id，还原不出六元组
    assert body["source_snapshots"] == [_SNAPSHOT]
    assert body["no_evidence"] is False


def test_no_evidence_is_derived_from_empty_evidence() -> None:
    """拒答路径的引用与依赖都是空集：不需要为此多存一列 ``no_evidence``。"""
    refused = _record(citations=[], knowledge_deps=[])
    body = chat_service._history_message(HistoryMessage(record=refused, content_visible=True))
    assert body["no_evidence"] is True
    assert body["citations"] == []
    assert body["source_snapshots"] == []


def test_user_message_has_no_evidence_fields() -> None:
    """用户消息没有引用与依赖：多给空数组会被当成「当时的证据是空」。"""
    body = chat_service._history_message(HistoryMessage(record=_record(role="user"), content_visible=True))
    assert "citations" not in body
    assert "source_snapshots" not in body
    assert "no_evidence" not in body


def test_completion_receipt_validates_and_ledger_drops_answer() -> None:
    """#14 回执能过响应模型；写台账时去掉 ``answer``（DR-08 不存正文）。"""
    body = {
        "request_id": "req-1",
        "session_id": "session-1",
        "user_message_id": "user-1",
        "assistant_message_id": "assistant-1",
        "status": COMPLETION_STATUS_COMPLETED,
        "answer": "普通申请 1 个工作日内完成审核。",
        "citations": [dict(_CITATION)],
        "source_snapshots": [dict(_SNAPSHOT)],
        "authorization_scope_id": "scope-1",
        "no_evidence": False,
        "finish_reason": "stop",
    }
    assert ChatCompletionResponse(**body).model_dump() == body

    ref = chat_service._completion_ref(body)
    assert "answer" not in ref
    assert ref["assistant_message_id"] == "assistant-1"
    assert ref["authorization_scope_id"] == "scope-1"


def test_ledger_status_maps_to_contract_status() -> None:
    """库内状态 → 对外状态的映射集中在契约层：过期按「已作废」而不是「失败」给。"""
    mapping = chat_service._LEDGER_TO_CONTRACT_STATUS
    assert mapping[STATUS_RUNNING] == "pending"
    assert mapping[STATUS_SUCCEEDED] == COMPLETION_STATUS_COMPLETED
    assert mapping[STATUS_FAILED] == "failed"
    assert mapping[STATUS_CANCELLED] == "cancelled"
    assert mapping[STATUS_EXPIRED] == "cancelled"


def test_error_detail_falls_back_to_internal_error() -> None:
    """台账里的失败码可能来自旧版本：不在册一律按内部错误给，话术与 retryable 同源。"""
    unknown = chat_service._error_detail("NOT_IN_TABLE")
    assert unknown["code"] == contract_errors.INTERNAL_ERROR
    assert unknown["retryable"] is (contract_errors.INTERNAL_ERROR in contract_errors.RETRYABLE_CODES)

    limited = chat_service._error_detail(contract_errors.RATE_LIMITED)
    assert limited == {
        "code": contract_errors.RATE_LIMITED,
        "message": contract_errors.DEFAULT_MESSAGES[contract_errors.RATE_LIMITED],
        "retryable": True,
    }
