"""提示词组装（``kb/chat/prompt.py``）回归：模板结构、证据正文口径、历史预算。

守住四条：

1. **消息结构不变**：``system(业务提示词 + 格式约束) → 历史 → user(参考资料 + 本轮问题)``，
   证据与问题仍在同一条 user 消息里、仍在历史之后（模型对最近输入最敏感）；
2. **给模型的是完整切片正文**，不是引用用的 160 字截断片段——否则 ``KB_CONTEXT_MAX_CHARS``
   永远用不上，模型只能拿半截切片硬答；
3. **上下文按整条证据截断**：预算装不下的证据整条跳过，不给半条残文；
4. **历史按「条数 + 字符预算」双重裁剪**：只按条数裁时，一条超长回答就能把本轮证据挤出去。
"""

from langchain_core.messages import HumanMessage, SystemMessage

from app.config.setting import settings
from app.modules.kb.chat.prompt import build_messages, history_turns, recall_block
from app.modules.kb.chat.schema import ChatEvidence

LONG_BODY = "开通流程第一步：在服务台提交申请。" + "后续步骤见下。" * 40


def _evidence(index: int, content: str, *, quote: str = "", title: str = "", score: float = 0.9) -> ChatEvidence:
    """一条证据：``quote`` 缺省取正文前 8 字，模拟「引用短、正文本长」的真实形状。"""
    return ChatEvidence(
        index=index,
        resource_type="document",
        resource_id=f"doc-{index}",
        publication_id="pub-1",
        record_id="rec-1",
        version_id="1",
        revision=1,
        chunk_id=f"rec-1:{index}",
        quote=quote or content[:8],
        content=content,
        title=title,
        score=score,
    )


def _messages(evidences: list[ChatEvidence], history: list[tuple[str, str]] | None = None) -> list[object]:
    return build_messages(
        prompt_text="你是企业知识库问答助手。",
        language="中文",
        evidences=evidences,
        history=history or [],
        question="如何申请 VPN？",
    )


def test_messages_keep_system_history_question_order() -> None:
    """系统提示词含业务约束与格式边界；历史按角色映射为消息类；最后一条是带证据的问题。"""
    messages = _messages(
        [_evidence(1, "在服务台提交申请。")],
        history=[("user", "上一轮问题"), ("assistant", "上一轮回答")],
    )

    assert [type(item).__name__ for item in messages] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]
    system, last = messages[0], messages[-1]
    assert isinstance(system, SystemMessage)
    assert "你是企业知识库问答助手。" in system.content
    assert "【回答要求】" in system.content
    assert "回答使用中文" in system.content, "语言约束必须真的进到系统消息里"
    assert isinstance(last, HumanMessage)
    assert last.content.startswith("参考资料：")


def test_recall_block_carries_full_chunk_not_truncated_quote() -> None:
    """关键回归：进上下文的是完整切片正文，``quote`` 只用于引用回放。"""
    evidence = _evidence(1, LONG_BODY, quote=LONG_BODY[:8])

    block = recall_block([evidence])

    assert LONG_BODY in block, "模型必须看到完整切片，而不是引用用的短摘录"
    assert block.startswith("参考资料：")


def test_recall_block_drops_whole_evidence_beyond_budget(monkeypatch) -> None:
    """预算装不下的证据整条跳过：半条证据既不能引用，也不该占上下文。"""
    monkeypatch.setattr(settings, "KB_CONTEXT_MAX_CHARS", 60)
    evidences = [_evidence(1, "甲" * 50), _evidence(2, "乙" * 50)]

    block = recall_block(evidences)

    assert "[1]" in block
    assert "[2]" not in block


def test_recall_block_marks_empty_recall() -> None:
    """一条都没有时给明确的「本轮没有可用资料」，不留空白让模型自由发挥。"""
    assert recall_block([]) == "参考资料：（本轮没有检索到可用资料）\n\n"


def test_history_turns_keeps_only_recent_within_char_budget(monkeypatch) -> None:
    """从最新往旧累计字符：装不下的旧消息整条丢掉，剩下的仍按时间正序。"""
    monkeypatch.setattr(settings, "KB_HISTORY_TURNS", 4)
    monkeypatch.setattr(settings, "KB_HISTORY_MAX_CHARS", 10)
    records = [("user", "一二三四五"), ("assistant", "五六七八九十"), ("user", "最新问题")]

    assert history_turns(records) == [("assistant", "五六七八九十"), ("user", "最新问题")]


def test_history_turns_skips_oversized_newest(monkeypatch) -> None:
    """最新一条自己就超预算：它和更旧的都进不去，历史为空也好过挤掉本轮证据。"""
    monkeypatch.setattr(settings, "KB_HISTORY_TURNS", 4)
    monkeypatch.setattr(settings, "KB_HISTORY_MAX_CHARS", 10)

    assert history_turns([("user", "短问题"), ("assistant", "很长的回答" * 20)]) == []


def test_history_turns_respects_turn_limit(monkeypatch) -> None:
    """条数上限先裁：``KB_HISTORY_TURNS=0`` 表示整段历史不带。"""
    monkeypatch.setattr(settings, "KB_HISTORY_TURNS", 0)

    assert history_turns([("user", "问题"), ("assistant", "回答")]) == []
