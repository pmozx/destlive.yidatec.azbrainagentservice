"""引用编号后处理：校验正文里的 ``[n]`` 是否落在本轮引用范围内。

唯一保留的能力是**越界编号摘除**（生成后校验）：模型编造 ``[9]`` 而本轮只有 5 条引用时，
这里立刻能测出来，且只摘编号、正文不整段作废。

引用**快照**不在这里构建：条目由业务域的证据对象 ``to_citation()`` 直接投影成契约八元组
（编号与证据一一对应、截断后可逐字校验）。早先的 ``build_cites`` / ``build_snippet`` /
``build_followups`` 在生产侧已无调用方（检索返回完整切片、问题拓展未启用），按规范 T3
一并删除——留着就是「看着能用、其实没人用」的第二套口径。
"""

import re

# 引用编号：[1] / [12]（最多两位，避免把 [2026] 这类年份误判为引用）
_CITE_RE = re.compile(r"\[(\d{1,2})\]")


def sanitize_citations(content: str, cite_count: int) -> tuple[str, list[int]]:
    """校验并清理正文中的引用编号（越界编号摘除，不整段作废）。"""
    invalid: list[int] = []

    def _replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if 1 <= number <= cite_count:
            return match.group(0)
        invalid.append(number)
        return ""

    cleaned = _CITE_RE.sub(_replace, content or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned, sorted(set(invalid))


__all__ = ["sanitize_citations"]
