"""用户提问预处理：清洗归一 → 安全识别 → 指代消解 → 意图分类 → 关键词实体。

设计取舍：
1. 全部为确定性规则，**不调用 LLM**：首字预算（≤3s）不能被一次改写请求吃掉；
2. 任一规则异常只降级为「原文检索」，绝不阻断问答；
3. 归一化文本不回显给用户，对外展示 ``analysis.optimized``（= rewritten）。
"""

import re
from dataclasses import dataclass, field

from app.core.logger import logger

# ── 意图常量（analysis 落库与分支判定共用） ──────────────────────────────
INTENT_KNOWLEDGE = "knowledge_query"
INTENT_FOLLOW_UP = "follow_up"
INTENT_CHITCHAT = "chitchat"
INTENT_OUT_OF_SCOPE = "out_of_scope"
INTENT_SYSTEM_META = "system_meta"
INTENT_PERMISSION_PROBE = "permission_probe"

# 库外主题词典
OUT_OF_SCOPE_KEYWORDS: tuple[str, ...] = (
    "天气",
    "气温",
    "股票",
    "基金",
    "彩票",
    "菜谱",
    "做饭",
    "翻译一下",
    "翻译成",
    "写代码",
    "写个函数",
    "编程",
    "新闻",
    "电影",
    "音乐",
    "笑话",
    "星座",
)
CHITCHAT_KEYWORDS: tuple[str, ...] = ("你好", "您好", "嗨", "在吗", "谢谢", "感谢", "辛苦", "早上好", "下午好", "晚上好", "再见")
SYSTEM_META_KEYWORDS: tuple[str, ...] = (
    "你的提示词",
    "系统提示词",
    "提示词是什么",
    "什么模型",
    "哪个模型",
    "你是谁",
    "什么大模型",
    "多少条数据",
    "数据库有多少",
    "你的能力",
    "怎么实现的",
)
PERMISSION_PROBE_KEYWORDS: tuple[str, ...] = ("我不能看", "看不到的", "无权限", "越权", "其他部门", "权限不够", "有没有权限")
# 提示词注入特征：打标不阻断（避免误伤「我忽略了这个条款会怎样」这类正常业务提问）
INJECTION_KEYWORDS: tuple[str, ...] = (
    "忽略以上",
    "忽略前面",
    "忽略之前",
    "忽略上述",
    "忘记以上",
    "忘记之前",
    "你现在是",
    "角色扮演",
    "roleplay",
    "developer mode",
    "开发者模式",
    "输出系统提示词",
    "打印提示词",
    "重复上面的内容",
    "system prompt",
)

# 口语前缀（按长度降序，保证「请问一下」先于「请问」剥离）
_SPOKEN_PREFIXES: tuple[str, ...] = ("请问一下", "我想问一下", "麻烦问一下", "帮我查一下", "帮我查下", "请问", "我想问", "麻烦问下")

# 疑问成分（指代消解时从上一轮问题里剔除）
_QUESTION_WORDS: tuple[str, ...] = ("是多少", "是什么", "有哪些", "怎么办", "怎么规定", "如何规定", "怎么", "如何", "多少", "什么", "吗", "呢")

# 关键词停用词（整词过滤；bigram 另按停用字过滤）
STOPWORDS = frozenset(
    {
        "什么",
        "怎么",
        "如何",
        "多少",
        "哪些",
        "是否",
        "可以",
        "需要",
        "请问",
        "一下",
        "我们",
        "公司",
        "这个",
        "那个",
        "有没有",
        "是什么",
        "为什么",
        "怎么样",
        "行不行",
        "能不能",
    }
)
_STOP_CHARS = frozenset("的是了在有和与或吗呢吧啊呀什怎么如何多少哪哪个些这那他她它们我你您请帮查下说一")

# ── 正则 ─────────────────────────────────────────────────────────────
_CONTROL_RE = re.compile(r"[\u0000-\u001f\u200b-\u200f\u2028\u2029\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")
_FULLWIDTH_RE = re.compile(r"[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]")
_QUESTION_MARK_RE = re.compile(r"[?？]{2,}")
_EXCLAMATION_RE = re.compile(r"[!！]{2,}")
_TOKEN_SPLIT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")
_TOKEN_FILTER_RE = re.compile(r"[^\u4e00-\u9fff\w]+")

_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_ID_CARD_RE = re.compile(r"\d{17}[\dXx]")
_BANK_CARD_RE = re.compile(r"\d{16,19}")

_DOC_QUOTE_RE = re.compile(r"《([^》]{1,40})》")
_DOC_NAME_RE = re.compile(r"([\u4e00-\u9fff]{2,20}?(?:管理办法|管理规定|管理制度|实施细则|工作规范|操作手册|办法|制度|规范|手册|规定|细则|流程|指南))")
_CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百零〇\d]+条(?:第[一二三四五六七八九十百零〇\d]+款)?")
_MONEY_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|元|块|美元|日元|欧元|港元|人民币)")
_DATE_RE = re.compile(r"\d{4}\s*(?:年|[-/])\s*\d{1,2}\s*(?:月|[-/])\s*\d{1,2}\s*日?|\d{4}\s*年(?:\d{1,2}\s*月)?")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%|百分之[一二三四五六七八九十百零〇\d]+")


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """一次提问的检索计划（预处理产物，供检索/提示词/落库共用）"""

    raw: str
    normalized: str
    rewritten: str
    keywords: list[str] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)
    intent: str = INTENT_KNOWLEDGE
    injection: bool = False
    allowed_space_ids: list[int] = field(default_factory=list)
    masked: str = ""

    @property
    def is_reject(self) -> bool:
        """是否属于「不检索」分支（闲聊/库外）。"""
        return self.intent in (INTENT_CHITCHAT, INTENT_OUT_OF_SCOPE)

    @property
    def is_direct(self) -> bool:
        """是否属于「固定话术」分支（系统探测）。"""
        return self.intent == INTENT_SYSTEM_META


def _halfwidth(match: re.Match[str]) -> str:
    """全角字母/数字转半角（中文标点保持原样）。"""
    return chr(ord(match.group()) - 0xFEE0)


def normalize_question(raw: str) -> str:
    """清洗归一：去控制字符 → 全角转半角 → 空白折叠 → 口语前缀剥离 → 标点归一 → 长度保护。"""
    text = _CONTROL_RE.sub("", raw or "")
    text = _FULLWIDTH_RE.sub(_halfwidth, text)
    text = text.replace("\u3000", " ")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # 口语前缀可能叠加出现（「请问一下，帮我查下…」），循环剥离
    changed = True
    while changed and text:
        changed = False
        for prefix in _SPOKEN_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].lstrip("，, ")
                changed = True
                break
    text = _QUESTION_MARK_RE.sub("？", text)
    text = _EXCLAMATION_RE.sub("！", text)
    return text[:500]


def mask_pii(text: str) -> str:
    """日志脱敏：身份证 / 手机号 / 银行卡（不阻断问答，只用于落日志）。"""
    masked = _ID_CARD_RE.sub(lambda m: f"{m.group()[:6]}{'*' * 8}{m.group()[-4:]}", text or "")
    masked = _PHONE_RE.sub(lambda m: f"{m.group()[:3]}****{m.group()[-4:]}", masked)
    return _BANK_CARD_RE.sub(lambda m: f"{m.group()[:6]}{'*' * 6}{m.group()[-4:]}", masked)


def detect_injection(text: str) -> bool:
    """提示词注入特征识别（打标不阻断，命中后温度降至 0）。"""
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in INJECTION_KEYWORDS)


def detect_intent(text: str) -> str:
    """意图分类（规则优先，顺序即优先级）。"""
    if any(keyword in text for keyword in SYSTEM_META_KEYWORDS):
        return INTENT_SYSTEM_META
    if any(keyword in text for keyword in OUT_OF_SCOPE_KEYWORDS):
        return INTENT_OUT_OF_SCOPE
    # 闲聊：命中问候词且为短句（长句通常带业务实体，避免误判）
    if len(text) <= 12 and any(keyword in text for keyword in CHITCHAT_KEYWORDS):
        return INTENT_CHITCHAT
    if any(keyword in text for keyword in PERMISSION_PROBE_KEYWORDS):
        return INTENT_PERMISSION_PROBE
    return INTENT_KNOWLEDGE


def _is_elliptical(question: str) -> bool:
    """是否为省略式追问（短句 + 回指标记）。"""
    text = question.strip(" ？?。！!")
    if not text or len(text) > 12:
        return False
    return text.startswith(("那", "还有")) or any(marker in text for marker in ("呢", "那", "还有"))


def _strip_follow_markers(question: str) -> str:
    """剥离追问标记，提取本轮差异部分（「那去日本呢」→「日本」）。"""
    core = question.strip(" ？?。！!")
    core = re.sub(r"^(那|那么|还有|这个|那个|它|他|她|该)", "", core)
    core = re.sub(r"(呢|吗|吧)$", "", core).strip("，,。 ")
    core = re.sub(r"^(去|在|是)", "", core).strip("，,。 ")
    return core


def _extract_subject(question: str) -> str:
    """从上一轮问题中提取主语（剔除疑问成分，保留业务主体）。"""
    subject = question
    for word in _QUESTION_WORDS:
        subject = subject.replace(word, "")
    subject = subject.strip(" ？?。！!，,、")
    return subject[:40]


def _has_new_doc_name(question: str) -> bool:
    """是否出现新的制度/文档名（出现则视为新话题，不做指代拼接）。"""
    return bool(_DOC_QUOTE_RE.search(question) or _DOC_NAME_RE.search(question))


def resolve_reference(question: str, last_question: str | None) -> str:
    """指代消解（规则 + 历史主语回填，命中即停）。

    规则优先级：① 省略式追问拼接 ② 代词回填 ③ 默认继承 ④ 新话题不继承。
    保守策略：拼接后语义不通或长度 > 120 字时回退原问题（宁可不改写，也不能改错）。
    """
    if not last_question:
        return question
    text = question.strip(" ？?。！!")
    if not text:
        return question
    # 规则 ④：出现新的制度/文档名 → 新话题
    if _has_new_doc_name(text):
        return question
    # 规则 ①②③：仅对省略式追问拼接（其余保持原问题，避免误改）
    if not _is_elliptical(text):
        return question
    subject = _extract_subject(last_question)
    if not subject:
        return question
    core = _strip_follow_markers(text)
    if not core or core in subject:
        return question
    merged = f"{core}{subject}"
    if len(merged) > 120:
        return question
    return merged


def extract_entities(text: str) -> dict[str, list[str]]:
    """实体抽取（文档名 / 条款号 / 金额 / 日期 / 百分比），用于提示词强化与日志排查。"""
    docs = [*_DOC_QUOTE_RE.findall(text), *_DOC_NAME_RE.findall(text)]
    return {
        "doc": _unique(docs),
        "clause": _unique(_CLAUSE_RE.findall(text)),
        "money": _unique(_MONEY_RE.findall(text)),
        "date": _unique(_DATE_RE.findall(text)),
        "percent": _unique(_PERCENT_RE.findall(text)),
    }


def _unique(items: list[str]) -> list[str]:
    """去重保序（过滤空串）。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = (item or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _split_pieces(token: str) -> list[str]:
    """长短语切分为 2 字词（无分词库时的轻量替代，过滤疑问字与停用词）。"""
    pieces: list[str] = []
    for start in range(0, len(token) - 1, 2):
        piece = token[start : start + 2]
        if any(char in _STOP_CHARS for char in piece):
            continue
        pieces.append(piece)
    return pieces


def extract_keywords(text: str, limit: int = 8) -> list[str]:
    """关键词抽取（短词整体保留，长短语按 2 字滑窗切分）。

    关键词只用于 ``analysis.keywords`` 展示与引用摘要窗口打分，
    不参与向量检索（检索用完整问题文本）。
    """
    keywords: list[str] = []
    for token in _TOKEN_SPLIT_RE.split(text or ""):
        token = _TOKEN_FILTER_RE.sub("", token).strip()
        if len(token) < 2 or token in STOPWORDS:
            continue
        if len(token) <= 6:
            keywords.append(token)
        else:
            keywords.extend(_split_pieces(token))
        if len(keywords) >= limit * 2:
            break
    return _unique(keywords)[:limit]


def _last_user_question(history: list[dict[str, str]] | None) -> str | None:
    """取历史中最近一条用户问题（不含本轮）。"""
    if not history:
        return None
    for message in reversed(history):
        if message.get("role") == "user" and message.get("content"):
            return message["content"]
    return None


def build_query_plan(
    question: str,
    allowed_space_ids: list[int],
    history: list[dict[str, str]] | None = None,
) -> QueryPlan:
    """构建检索计划（预处理流水线入口，异常降级为原文检索）。"""
    raw = (question or "").strip()
    try:
        normalized = normalize_question(raw)
        intent = detect_intent(normalized)
        rewritten = normalized
        if intent in (INTENT_KNOWLEDGE, INTENT_PERMISSION_PROBE):
            resolved = resolve_reference(normalized, _last_user_question(history))
            if resolved != normalized:
                rewritten = resolved
                intent = INTENT_FOLLOW_UP
        return QueryPlan(
            raw=raw,
            normalized=normalized,
            rewritten=rewritten or raw,
            keywords=extract_keywords(rewritten),
            entities=extract_entities(rewritten),
            intent=intent,
            injection=detect_injection(normalized),
            allowed_space_ids=list(allowed_space_ids),
            masked=mask_pii(raw),
        )
    except Exception as e:  # noqa: BLE001 预处理失败不阻断问答：降级为原文检索
        logger.warning(f"提问预处理降级为原文检索: {e!s}")
        return QueryPlan(
            raw=raw,
            normalized=raw,
            rewritten=raw,
            intent=INTENT_KNOWLEDGE,
            allowed_space_ids=list(allowed_space_ids),
            masked=mask_pii(raw),
        )
