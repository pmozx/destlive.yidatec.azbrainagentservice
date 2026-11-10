"""智能体构建与正文流消费（规范 14.1.1：「统一使用 create_agent」的唯一落地点）。

为什么不许业务直接调 ``create_agent``：

1. **模型必须来自 ``LlmClient``**：``create_agent(model="deepseek-chat")`` 内部会走
   ``init_chat_model`` 的默认 provider 与环境变量，绕过自建 vLLM 的 ``base_url`` /
   ``http_async_client`` / 代理控制——本地能跑、换环境连不上。这里统一把
   :class:`ModelProfile` 解析成已配好的模型实例再交出去（与规范 L22 同一口径）；
2. **中间帧必须在这里丢弃**：``astream(stream_mode="messages")`` 产出 ``(chunk, metadata)``，
   含工具调用分片与工具结果消息；业务只该看到最终正文，逐处自写过滤迟早漏一种；
3. **用量按轮累加**：一次 agent 运行可能调多轮模型，每轮末片各带一份 usage，
   单轮「末片覆盖」的口径会把前面几轮算丢。

边界：本模块只管「怎么构建、怎么取正文」，不感知问答业务（引用编号、拒答、落库都在
``modules/kb``）。**单轮 RAG 直出不经过这里**——它没有分支决策，套 agent 只会多一层
开销与一批必须丢弃的中间帧（规范 L24 直出档）。
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.core.clients import get_llm_client
from app.core.clients.llm import message_text

from .profiles import ModelProfile


def build_agent(
    profile: ModelProfile,
    *,
    tools: Sequence[BaseTool] | None = None,
    system_prompt: str | SystemMessage | None = None,
    response_format: Any = None,
    middleware: Sequence[AgentMiddleware] = (),
    name: str | None = None,
) -> CompiledStateGraph:
    """按档案构建一张已编译的智能体图（全仓唯一允许出现 ``create_agent`` 的位置）。

    ``name`` 默认取用途标识：LangSmith 里按图名筛「这次工具决策属于哪条业务链路」，
    同一用途要区分实例时显式传入。

    **构建一次、复用多次**：每次调用都会重新编译 StateGraph，不要在请求处理里按请求构建；
    应当由所属模块持有（模块级单例或依赖注入缓存），与客户端单例同生命周期。

    模型实例由 ``LlmClient.chat_model(profile)`` 给出：档案里已带网关、密钥与默认参数，
    本函数不再单独转温度/token——那会与配置表的默认值形成第二个口径。
    """
    model = get_llm_client().chat_model(profile)
    return create_agent(
        model=model,
        tools=list(tools) if tools else None,
        system_prompt=system_prompt,
        response_format=response_format,
        middleware=list(middleware),
        name=name or profile.purpose,
    )


@dataclass(frozen=True, slots=True)
class AgentStreamChunk:
    """智能体正文分片：增量正文 + 截至当前的累计用量（直接可映射 SSE ``delta`` 帧）。"""

    delta: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


async def stream_agent_text(
    agent: CompiledStateGraph,
    messages: Sequence[BaseMessage],
    *,
    config: RunnableConfig | None = None,
) -> AsyncIterator[AgentStreamChunk]:
    """只转发模型正文与用量：工具调用分片、工具结果与无数据的聚合帧一律丢弃。

    分两类帧交给调用方：有正文的（可直接映射 SSE ``delta``）与**只有用量、正文为空**的
    （用途量更新指标，不发帧）。后者不能省：网关的 usage 总是跟在最后一片正文之后到达，
    把空正文帧全部丢掉会让最后一轮的 token 永远统计不到。

    用量按轮累加。前提与 :mod:`app.core.clients.llm` 一致：网关在**每次模型调用的末片**
    给全量 usage（OpenAI 兼容 ``stream_options.include_usage``）。若换成每片都带累计值的
    网关，累加会重复计入，换网关时须复核这里。

    ``config`` 原样透传给图：不透传时模型 run 会成为 trace 里的孤立根节点（规范 L25）。
    """
    tokens_in = tokens_out = 0
    async for chunk, _meta in agent.astream({"messages": list(messages)}, stream_mode="messages", config=config):
        if not isinstance(chunk, AIMessageChunk):
            continue
        usage = getattr(chunk, "usage_metadata", None) or {}
        tokens_in += int(usage.get("input_tokens") or 0)
        tokens_out += int(usage.get("output_tokens") or 0)
        text = message_text(chunk.content)
        if text:
            yield AgentStreamChunk(delta=text, tokens_in=tokens_in, tokens_out=tokens_out)
        elif usage:
            # 只有用量、没有正文：既可能是一轮的末片，也可能是运行结束的聚合帧
            yield AgentStreamChunk(tokens_in=tokens_in, tokens_out=tokens_out)


__all__ = ["AgentStreamChunk", "build_agent", "stream_agent_text"]
