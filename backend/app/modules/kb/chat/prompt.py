"""提示词组装：把「GSI 提示词 + 检索证据 + 合规历史 + 本轮问题」拼成模型消息体。

四条口径：

1. GSI 的 ``prompt_text`` 是**业务约束**（怎么答、答什么），本侧只追加**格式与安全边界**
   （怎么标注引用、没证据怎么办）。两者职责不同，因此是「拼接」而不是「二选一」。
2. 证据编号 ``[n]`` 在拼装时就定死，与 ``citations`` 一一对应；后处理只摘除越界编号，
   绝不重排——重排会让正文里的 ``[2]`` 指向另一条证据，等于篡改引用。
3. 证据与历史**各有独立字符预算**（``KB_CONTEXT_MAX_CHARS`` / ``KB_HISTORY_MAX_CHARS``）：
   共用一份预算时，长会话里的历史会把本轮证据挤出去，模型就变成「有对话无依据」。
4. 历史按「最近 ``KB_HISTORY_TURNS`` 条 + ``KB_HISTORY_MAX_CHARS`` 字」双重裁剪，从最新往旧
   累计——只按条数裁时，一条超长回答就能把本轮证据挤出上下文。且产出标准消息对象
   而非 ``{"role", "content"}`` 字典（规范 L23）。
"""

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.config.setting import settings

from ..chat_message.schema import MESSAGE_ASSISTANT
from .schema import ChatEvidence

#: 追加在 GSI 提示词之后的格式约束（业务约束由调用方给，这里只补输出格式与安全边界）
_OUTPUT_RULES = """【回答要求】
1. 只依据下方「参考资料」回答；资料未覆盖的内容请直接说明无法从现有资料中得到答案，不得用常识或猜测补充企业规则。
2. 引用资料时在句末标注编号，例如 [1]；可同时引用多条，例如 [1][3]。编号只能使用参考资料中出现过的编号，不得编造。
3. 回答使用{language}，先给结论再给步骤；不要输出 Markdown 表格。"""


#: 单轮模板：系统（业务约束 + 格式边界）→ 历史 → 本轮（证据 + 问题）。
#: 用模板而不是 f-string 拼串：变量清单由模板显式声明，漏传/多传在 ``format_messages`` 立刻报错，
#: 而不是静默拼出「语言约束丢了」这种只能靠肉眼发现的提示词。
_TURN_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", "{business}\n\n" + _OUTPUT_RULES),
        MessagesPlaceholder("history"),
        ("human", "{recall}问题：{question}"),
    ]
)


def recall_block(evidences: Sequence[ChatEvidence]) -> str:
    """参考资料块：``[n] 标题`` + 切片正文，累计到字符上限即停。

    截断按「整条证据」而不是「整段文本」：半条证据既不能引用也不该占上下文，
    所以宁可少给一条，也不给一条残文。提示词与证据二判共用这一份文本——两处口径
    一旦分叉，「模型看到的」和「质检判过的」就不是同一批资料了。
    """
    budget = max(0, settings.KB_CONTEXT_MAX_CHARS)
    parts: list[str] = []
    for evidence in evidences:
        header = f"[{evidence.index}] {evidence.title}".strip()
        # 给模型的是完整切片正文；``quote`` 只是引用那一小段（截断后仍可逐字校验）
        block = f"{header}\n{evidence.content or evidence.quote}"
        if budget - len(block) <= 0:
            break
        budget -= len(block)
        parts.append(block)
    if not parts:
        return "参考资料：（本轮没有检索到可用资料）\n\n"
    return "参考资料：\n" + "\n\n".join(parts) + "\n\n"


def build_messages(
    *,
    prompt_text: str,
    language: str,
    evidences: Sequence[ChatEvidence],
    history: Sequence[tuple[str, str]],
    question: str,
) -> list[BaseMessage]:
    """拼装消息体：``system(GSI 提示词 + 格式约束) → 历史 → user(证据 + 本轮问题)``。

    证据与问题放在**同一条** user 消息里且位于历史之后：模型对最近的输入最敏感，
    把证据甩在早期历史之前，长会话里会被当成背景知识而不是本轮依据。
    """
    return _TURN_TEMPLATE.format_messages(
        business=prompt_text.strip(),
        language=language or "中文",
        # 角色→类映射只这一处：按库里的常量判定，避免裸字面量 "assistant" 写错就静默降级成用户发言
        history=[
            AIMessage(content=text) if role == MESSAGE_ASSISTANT else HumanMessage(content=text) for role, text in history
        ],
        recall=recall_block(evidences),
        question=question.strip(),
    )


def history_turns(records: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """把落库消息裁剪成「最近 N 条 + 字符预算内」，并保持正序。

    先按条数取最近 N 条，再从最新往旧累计字符，超出 ``KB_HISTORY_MAX_CHARS`` 即停：
    只按条数裁时，一条超长回答就能把本轮证据挤出上下文，「先砍证据后砍历史」的口径就反了。
    用字符而非真 token：本地没有分词器，中文 1 字≈1 token，按字数算是更保守的估算。
    """
    limit = max(0, settings.KB_HISTORY_TURNS)
    if limit == 0:
        return []
    budget = max(0, settings.KB_HISTORY_MAX_CHARS)
    kept: list[tuple[str, str]] = []
    for role, text in reversed(records[-limit:]):
        cost = len(text or "")
        if cost > budget:
            break
        budget -= cost
        kept.append((role, text))
    return kept[::-1]
