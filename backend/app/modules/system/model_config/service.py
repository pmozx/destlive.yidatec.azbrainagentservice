"""模型配置服务：页面维护（增删改查 / 启停 / 设默认）与三类模型的连通性测试。

四件事只有本层做，别处不要复制：

1. **写库后清快照 + 重建连接类客户端**：``app.core.agents.registry`` 是进程内 TTL 快照，
   改完配置不清它，运维要等一个 TTL 才能看到效果（还会以为「点了没生效」）；嵌入 / 重排序
   客户端把 base_url 与密钥存在实例上，也必须重建，否则页面上改了地址仍在打旧网关；
2. **默认模型是「每类一个」的单例**：``is_default`` 只在同一 ``model_type`` 内唯一——
   嵌入行被设为默认时不能顺手把对话行的默认清理掉；
3. **默认参数按类型兜底**：新建时 ``default_config_json`` 留空就填该类型的推荐值，
   空 ``{}`` 会让页面看不到任何可调项，新人以为「没东西可配」；
4. **连通性测试不落库**：按类型发一次最小请求（对话问一句 / 嵌入算一条 / 重排序排两条），
   用于改完配置当场验证；真实业务的失败仍然只从业务日志里看。

空密钥的语义：更新时 ``api_key`` 传空串 = 沿用原值（页面只回显掩码，不能要求运维
每次粘贴完整密钥）；新增时空串 = 沿用同类 env（LLM / EMBEDDING / RERANK 各自的 API_KEY）。
"""

import asyncio
from time import perf_counter
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agents import PURPOSE_KB_ANSWER, build_profile
from app.core.agents.registry import invalidate_model_config_cache
from app.core.base_schema import PageResultSchema
from app.core.clients import EmbeddingClient, RerankClient, get_llm_client, reset_model_clients
from app.core.clients.embedding import embedding_provider
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from .crud import SystemModelConfigCRUD
from .model import MODEL_TYPE_EMBEDDING, MODEL_TYPE_LABELS, MODEL_TYPE_RERANK, SystemModelConfigModel
from .schema import (
    SystemModelConfigCreateSchema,
    SystemModelConfigOutSchema,
    SystemModelConfigQueryParam,
    SystemModelConfigTestOutSchema,
    SystemModelConfigUpdateSchema,
    check_config_keys,
    default_config_for,
)

#: 连通性测试的总预算（秒）：模型首字慢是常态，但页面不能无限转圈
TEST_TIMEOUT_SECONDS = 30.0
#: 测试请求的两个固定参数：只要它「能说话」，不关心答得好不好
_TEST_OVERRIDES = {"temperature": 0.0, "max_tokens": 32}
_TEST_PROMPT = "连通性测试：请只回复「ok」。"
#: 嵌入 / 重排序的探测输入：短、无歧义，失败只可能是配置或服务问题
_TEST_INPUT = "连通性测试"
#: 回复截断长度：够确认它不是空答，又不会把页面撑开
_REPLY_LIMIT = 200


class SystemModelConfigService:
    """模型配置的页面侧服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.crud = SystemModelConfigCRUD(db)

    # ── 查询 ──────────────────────────────────────────────────────────

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SystemModelConfigQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SystemModelConfigOutSchema]:
        """分页查询：默认按排序号升序（页面拖排序后立即体现）。"""
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"sort": "asc"}, {"id": "asc"}],
            search=search_to_dict(search) or {},
            out_schema=SystemModelConfigOutSchema,
        )

    async def detail(self, id: int) -> SystemModelConfigOutSchema:
        """单条详情（不含明文密钥）。"""
        return await self.crud.get_or_404(
            id=id,
            msg="模型配置不存在",
            out_schema=SystemModelConfigOutSchema,
        )

    # ── 写入 ──────────────────────────────────────────────────────────

    async def create(self, data: SystemModelConfigCreateSchema) -> SystemModelConfigOutSchema:
        """新增模型配置：标识唯一、同类型默认唯一、默认参数按类型兜底。"""
        await self._ensure_key_available(data.model_config_key)
        if data.is_default and not data.enabled:
            raise CustomException(msg="停用的模型不能设为默认模型")
        if data.is_default:
            await self.crud.clear_other_defaults(data.model_type)
        payload = data.model_copy(
            update={"default_config_json": data.default_config_json or default_config_for(data.model_type)}
        )
        obj = await self.crud.create(payload)
        invalidate_model_config_cache()
        await reset_model_clients()
        logger.info(
            "新增模型配置：type={} key={} model={} enabled={}",
            obj.model_type,
            obj.model_config_key,
            obj.model_name or obj.model_config_key,
            obj.enabled,
        )
        return SystemModelConfigOutSchema.model_validate(obj)

    async def update(self, id: int, data: SystemModelConfigUpdateSchema) -> SystemModelConfigOutSchema:
        """更新模型配置：``api_key`` 传空串沿用原值；停用默认行会顺手清掉默认标记。"""
        current = await self.crud.get_or_404(id=id, msg="模型配置不存在")
        if data.model_config_key is not None and data.model_config_key != current.model_config_key:
            await self._ensure_key_available(data.model_config_key, exclude_id=id)

        payload = data.model_dump(exclude_unset=True, exclude_none=True)
        # 空密钥 = 沿用原值：页面只回显掩码，把空串直接写库会静默清掉密钥
        if not str(payload.get("api_key") or "").strip():
            payload.pop("api_key", None)

        # 类型可能同时被改（例如把对话行误改成了嵌入）：白名单用改后的类型判定
        target_type = str(payload.get("model_type") or current.model_type)
        if "default_config_json" in payload:
            try:
                check_config_keys(payload["default_config_json"], target_type)
            except ValueError as exc:
                raise CustomException(msg=str(exc)) from exc

        target_default = payload.get("is_default")
        target_enabled = payload.get("enabled", current.enabled)
        if target_default and not target_enabled:
            raise CustomException(msg="停用的模型不能设为默认模型")
        if target_default or (current.is_default and target_type != current.model_type):
            # 改成新类型时，原来的默认标记就落到了新类型上；新类型可能已有默认行，必须清掉
            await self.crud.clear_other_defaults(target_type, keep_id=id)
        elif target_enabled is False and current.is_default:
            # 停用默认行：默认标记跟着关掉，避免「停用了却还是默认」的矛盾状态
            payload["is_default"] = False
            logger.info("默认模型被停用，已清除默认标记：key={}", current.model_config_key)

        obj = await self.crud.update(id, payload)
        invalidate_model_config_cache()
        await reset_model_clients()
        logger.info(
            "更新模型配置：type={} key={} 变更字段={}",
            obj.model_type,
            obj.model_config_key,
            ", ".join(sorted(payload)) or "-",
        )
        return SystemModelConfigOutSchema.model_validate(obj)

    async def delete(self, ids: list[int]) -> None:
        """批量物理删除：默认模型要先切走（删了它，该类型就只剩 env 兜底）。"""
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        defaults = [row for row in await self.crud.get_list(search={"id": ("in", ids)}) if row.is_default]
        if defaults:
            labels = "、".join(
                f"{MODEL_TYPE_LABELS.get(row.model_type, row.model_type)} {row.model_config_key}" for row in defaults
            )
            raise CustomException(msg=f"默认模型不能删除（{labels}），请先把该类型的默认切到其他模型")
        await self.crud.delete(ids=ids)
        invalidate_model_config_cache()
        await reset_model_clients()
        logger.info("删除模型配置：ids={}", ids)

    # ── 连通性测试 ────────────────────────────────────────────────────

    async def test_connection(self, id: int) -> SystemModelConfigTestOutSchema:
        """按该行配置发一次最小请求（对话 / 嵌入 / 重排序各走各的探测方式）。

        为什么不抛异常：页面点「测试」要的是「通/不通 + 为什么」，抛 5xx 只会得到一句
        「服务器错误」；真正的配置错误（地址空、模型名为空、协议不支持）也在这里变成可读文案。
        """
        row = await self.crud.get_or_404(id=id, msg="模型配置不存在")
        if row.model_type == MODEL_TYPE_EMBEDDING:
            return await self._test_embedding(row)
        if row.model_type == MODEL_TYPE_RERANK:
            return await self._test_rerank(row)
        return await self._test_chat(row)

    async def _test_chat(self, row: SystemModelConfigModel) -> SystemModelConfigTestOutSchema:
        """对话探测：问一句、要它回 ok（走完整档案，顺带验证合并后的参数能否构造客户端）。"""
        profile = build_profile(
            PURPOSE_KB_ANSWER,
            key=row.model_config_key,
            model=row.model_name or row.model_config_key,
            provider=row.model_provider,
            base_url=row.base_url,
            api_key=row.api_key,
            model_defaults=row.default_config_json,
        )
        started = perf_counter()
        try:
            reply = await asyncio.wait_for(
                get_llm_client().chat(
                    [HumanMessage(content=_TEST_PROMPT)],
                    profile=profile,
                    overrides=_TEST_OVERRIDES,
                ),
                timeout=TEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 测试失败是结果，不是服务故障
            elapsed = int((perf_counter() - started) * 1000)
            logger.warning("模型连通性测试失败：key={} 耗时={}ms {}", row.model_config_key, elapsed, exc)
            return SystemModelConfigTestOutSchema(
                ok=False,
                model=profile.label,
                latency_ms=elapsed,
                error=_readable_error(exc),
            )
        elapsed = int((perf_counter() - started) * 1000)
        logger.info("模型连通性测试成功：key={} 耗时={}ms", row.model_config_key, elapsed)
        return SystemModelConfigTestOutSchema(
            ok=True,
            model=profile.label,
            latency_ms=elapsed,
            reply=reply.strip()[:_REPLY_LIMIT],
        )

    async def _test_embedding(self, row: SystemModelConfigModel) -> SystemModelConfigTestOutSchema:
        """嵌入探测：算一条短文本，成功时回报维度——维度与 VECTOR_DIM 不一致能当场看出来。"""
        label = row.model_name or row.model_config_key
        started = perf_counter()
        client: EmbeddingClient | None = None
        try:
            client = EmbeddingClient(
                row.base_url or None,
                provider=embedding_provider(row.model_provider),
                api_key=row.api_key or None,
                model=label,
                timeout=_seconds(row.default_config_json.get("timeout")),
                # 测试只算一条：不并发、不重试，失败原因要快速、准确地暴露
                batch_size=1,
                concurrency=1,
                max_retries=0,
            )
            vectors = await asyncio.wait_for(client.embed_texts([_TEST_INPUT]), timeout=TEST_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 测试失败是结果，不是服务故障
            elapsed = int((perf_counter() - started) * 1000)
            logger.warning("嵌入模型连通性测试失败：key={} 耗时={}ms {}", row.model_config_key, elapsed, exc)
            return SystemModelConfigTestOutSchema(
                ok=False, model=label, latency_ms=elapsed, error=_readable_error(exc)
            )
        finally:
            if client is not None:
                await client.aclose()
        elapsed = int((perf_counter() - started) * 1000)
        logger.info("嵌入模型连通性测试成功：key={} 耗时={}ms", row.model_config_key, elapsed)
        return SystemModelConfigTestOutSchema(
            ok=True,
            model=label,
            latency_ms=elapsed,
            reply=f"向量维度 {len(vectors[0])}",
        )

    async def _test_rerank(self, row: SystemModelConfigModel) -> SystemModelConfigTestOutSchema:
        """重排序探测：两个候选排一次，成功时回报 top1 分数（报文结构不对会立即失败）。"""
        label = row.model_name or row.model_config_key
        started = perf_counter()
        try:
            client = RerankClient(
                base_url=row.base_url or None,
                api_key=row.api_key or None,
                model=label,
                timeout=_seconds(row.default_config_json.get("timeout")),
            )
            hits = await asyncio.wait_for(
                client.rerank(_TEST_INPUT, [_TEST_INPUT, "无关文本"], top_n=1),
                timeout=TEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 测试失败是结果，不是服务故障
            elapsed = int((perf_counter() - started) * 1000)
            logger.warning("重排序模型连通性测试失败：key={} 耗时={}ms {}", row.model_config_key, elapsed, exc)
            return SystemModelConfigTestOutSchema(
                ok=False, model=label, latency_ms=elapsed, error=_readable_error(exc)
            )
        elapsed = int((perf_counter() - started) * 1000)
        logger.info("重排序模型连通性测试成功：key={} 耗时={}ms", row.model_config_key, elapsed)
        top = hits[0] if hits else None
        reply = f"返回 {len(hits)} 条结果"
        if top is not None:
            reply = f"{reply}，top1 相关度 {top.score:.4f}"
        return SystemModelConfigTestOutSchema(ok=True, model=label, latency_ms=elapsed, reply=reply)

    # ── 内部辅助 ──────────────────────────────────────────────────────

    async def _ensure_key_available(self, model_config_key: str, *, exclude_id: int | None = None) -> None:
        """模型标识唯一：业务配置都按它取档案，重名等于随机挑一行。"""
        if await self.crud.get_by_config_key(model_config_key, exclude_id=exclude_id):
            raise CustomException(msg=f"模型标识已存在：{model_config_key}")


def _seconds(value: Any) -> float | None:
    """配置表里的数值项（jsonb 存的是 JSON number）：类型不对就当没配，交给客户端用 env。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _readable_error(exc: Exception) -> str:
    """把异常压成一句可读文案：带异常类名（区分超时/鉴权/连不上），去掉多余的类路径。"""
    if isinstance(exc, TimeoutError):
        return f"超时：{TEST_TIMEOUT_SECONDS:.0f} 秒内没有拿到完整回复（检查网关与该模型的响应速度）"
    if isinstance(exc, CustomException):
        return exc.msg
    return f"{type(exc).__name__}: {str(exc)[:300]}"


__all__ = ["TEST_TIMEOUT_SECONDS", "SystemModelConfigModel", "SystemModelConfigService"]
