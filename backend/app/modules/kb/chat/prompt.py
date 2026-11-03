"""提示词组装：把「GSI 提示词 + 检索证据 + 合规历史 + 本轮问题」拼成模型消息体。

四条口径：

1. GSI 的 ``prompt_text`` 是**业务约束**（怎么答、答什么），本侧只追加**格式与安全边界**
   （怎么标注引用、没证据怎么办）。两者职责不同，因此是「拼接」而不是「二选一」。
2. 证据编号 ``[n]`` 在拼装时就定死，与 ``citations`` 一一对应；后处理只摘除越界编号，
   绝不重排——重排会让正文里的 ``[2]`` 指向另一条证据，等于篡改引用。
3. 上下文按 ``KB_CONTEXT_MAX_CHARS`` 累计截断，且**先砍证据后砍历史**：证据是答案依据，
   历史只是语气与指代；反过来会让模型拿着完整对话却无据可答。
4. 历史只带最近 ``KB_HISTORY_TURNS`` 条：固定成本不随会话长度线性增长，也避免旧话题
   把本轮证据挤出注意力。
"""

from collections.abc import Sequence

from app.config.setting import settings
from app.core.clients.llm import ChatMessage

from .schema import ChatEvidence

#: 追加在 GSI 提示词之后的格式约束（业务约束由调用方给，这里只补输出格式与安全边界）
_OUTPUT_RULES = """【回答要求】
1. 只依据下方「参考资料」回答；资料未覆盖的内容请直接说明无法从现有资料中得到答案，不得用常识或猜测补充企业规则。
2. 引用资料时在句末标注编号，例如 [1]；可同时引用多条，例如 [1][3]。编号只能使用参考资料中出现过的编号，不得编造。
3. 回答使用{language}，先给结论再给步骤；不要输出 Markdown 表格。"""


def _evidence_block(evidences: Sequence[ChatEvidence]) -> str:
    """证据块：``[n] 标题（发布单元）`` + 正文，累计到字符上限即停。

    截断按「整条证据」而不是「整段文本」：半条证据既不能引用也不该占上下文，
    所以宁可少给一条，也不给一条残文。
    """
    budget = max(0, settings.KB_CONTEXT_MAX_CHARS)
    parts: list[str] = []
    for evidence in evidences:
        header = f"[{evidence.index}] {evidence.title}".strip()
        block = f"{header}\n{evidence.quote}"
        if budget - len(block) <= 0:
            break
        budget -= len(block)
        parts.append(block)
    return "\n\n".join(parts)


def build_messages(
    *,
    prompt_text: str,
    language: str,
    evidences: Sequence[ChatEvidence],
    history: Sequence[tuple[str, str]],
    question: str,
) -> list[ChatMessage]:
    """拼装消息体：``system(GSI 提示词 + 格式约束) → 历史 → user(证据 + 本轮问题)``。

    证据与问题放在**同一条** user 消息里且位于历史之后：模型对最近的输入最敏感，
    把证据甩在早期历史之前，长会话里会被当成背景知识而不是本轮依据。
    """
    system = f"{prompt_text.strip()}\n\n{_OUTPUT_RULES.format(language=language or '中文')}"
    messages: list[ChatMessage] = [{"role": "system", "content": system}]
    for role, content in history:
        messages.append({"role": role, "content": content})

    block = _evidence_block(evidences)
    evidence_part = f"参考资料：\n{block}\n\n" if block else "参考资料：（本轮没有检索到可用资料）\n\n"
    messages.append({"role": "user", "content": f"{evidence_part}问题：{question.strip()}"})
    return messages


def history_turns(records: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """把落库消息裁剪成「最近 N 条」，并保持正序。"""
    limit = max(0, settings.KB_HISTORY_TURNS)
    if limit == 0:
        return []
    return list(records[-limit:])
