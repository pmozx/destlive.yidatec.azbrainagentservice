"""证据充分性二判（结构化输出）：三闸过关但相关度偏低时，让模型判一次「能不能答」。

为什么需要它：条数闸只看「有几条」、绝对分闸看「够不够高」，两者都是在 0–1 分数空间里
拍的阈值。召回把几段「勉强相关」的切片塞进来时，分数可能刚好过线，模型于是拿着半相关
材料硬答——答案读起来像真的，却答的是邻近的另一个问题。一次结构化判定（``sufficient``
布尔 + 缺什么）能把这类轮次拦到拒答分支上。

四条约束：

1. **默认关闭**：多一次模型调用就多一次延迟与费用，只在「过线但可疑」的灰区才触发
   （``KB_GRADE_JUDGE_ENABLE`` + ``KB_GRADE_JUDGE_TOP_SCORE``）；
2. **失败不升级**：超时、上游异常、结构化解析失败一律按「资料够答」处理。判定是增强，
   不是门禁——因为质检挂了而拒答，是把可用系统降级成不可用；
3. **不替代三闸**：条数与绝对分闸先跑，本模块只覆盖灰区；命中的资料文本与提示词同源
   （``chat/prompt.recall_block``），保证「模型看到的」就是「质检判过的」；
4. 只依赖标准消息与文本，不 import 任何业务模型（与 ``rerank`` / ``cite`` 同口径）。
"""

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config.setting import settings
from app.core.clients import get_llm_client
from app.core.logger import logger


class SufficiencyVerdict(BaseModel):
    """结构化判定结果：能不能答 + 缺什么（``missing`` 只进日志与 trace，不进响应）。"""

    sufficient: bool = Field(description="现有资料是否足以回答该问题；只要关键信息缺失就是 false")
    missing: str = Field(default="", description="若资料不足，说明缺少哪类信息；资料足够时留空")


_SYSTEM = """你是企业知识库问答的质检员。判断给定「参考资料」是否足以回答「问题」。
判 false 的标准：参考资料只覆盖问题的边缘或部分条件，要答出来必须靠常识或推测补全；
判 true 的标准：参考资料直接给出了回答问题所需的关键事实。只输出判定，不要回答问题。"""


async def judge_sufficiency(question: str, recall: str) -> tuple[bool, str]:
    """判「资料够不够答」，返回 ``(是否足够, 缺什么)``。

    判定本身失败时返回 ``(True, "")``：宁可按原样回答，也不要因为质检挂了而拒答。
    """
    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=f"{recall}问题：{question.strip()}"),
    ]
    try:
        model = get_llm_client().chat_model().with_structured_output(SufficiencyVerdict)
        verdict = await asyncio.wait_for(model.ainvoke(messages), settings.KB_GRADE_JUDGE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 质检失败不升级：判定是增强，不是门禁
        logger.warning("证据二判失败，按「资料够答」处理（不影响本轮回答）：{}", exc)
        return True, ""
    if not isinstance(verdict, SufficiencyVerdict):
        # 兼容返回 dict 的实现（部分 provider 的 json 模式不还原 pydantic 实例）
        try:
            verdict = SufficiencyVerdict(**dict(verdict))
        except Exception as exc:  # noqa: BLE001 同上
            logger.warning("证据二判返回结构不可解析，按「资料够答」处理：{}", exc)
            return True, ""
    return verdict.sufficient, verdict.missing


__all__ = ["SufficiencyVerdict", "judge_sufficiency"]
