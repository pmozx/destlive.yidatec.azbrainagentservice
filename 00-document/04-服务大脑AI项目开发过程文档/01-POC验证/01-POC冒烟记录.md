# 01 · POC 冒烟记录

> 执行时间：2026-10-29 15:27（单轮基线，`--reset` 后首轮）
> 服务：本机 `uvicorn app:create_app --factory --host 127.0.0.1 --port 8003`
> 调用方：`app.poc.mock_client`（client_id=`gsi-service`，自签测试密钥，读 `CONTRACT_V1_SHARED_SECRET` 这组共用配置）
> 结果：**17 条用例，通过 17 条，失败 0 条**
>
> **历史证据说明**：本记录是 POC 期（真实业务实现）的基线，文中 `app.poc.mock_client`、`--reset`、`poc_v1_metric`
> 已于 2026-10-30 随 POC 下线（`app/poc/` 已删除、临时表已清理）；当前复跑命令见 `README.md` §四（`app.contract.mock_gsi --allow-skeleton`）。本文不覆盖重写，保留原样对照。

---

## 一、环境与前提

| 项 | 值 |
| --- | --- |
| 数据库 | PostgreSQL @ `192.168.29.120:39990/service_brain`（启用 pgvector） |
| 向量模型 | bge-m3 @ `http://192.168.29.120:11434`（`EMBEDDING_PROTOCOL=ollama`，维度 1024） |
| 生成模型 | `deepseek-ai/DeepSeek-V4-Flash` @ `https://api.siliconflow.cn/v1`（OpenAI 兼容，`LLM_TIMEOUT=60s`） |
| POC 配置 | `POC_V1_ENABLE=true`、签名窗口 ±300000ms、RRF k=60、召回 TOP_K=20 / 进入上下文 TOP_N=8 |
| 样例文档 | 本地临时 HTTP 服务托管的 `vpn-guide.md`（3 小节，真实可拉取的 `file_url`） |
| 切片集合 | `#03` 由 GSI 侧生成 3 条切片（`chunk-1..3`），AI 侧只做校验 + 向量化 + 落库 |

> 样例文档刻意用**真实 HTTP 地址**而非假链接：契约要求 `file_url` 是 AI 能访问的地址，用假链接测不出拉取链路的缺陷。

---

## 二、用例明细

| # | 用例 | 期望 HTTP | 实际 HTTP | 结论 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 1 | 签名·缺少签名头 | 401 | 401 | 通过 | 错误码 `unauthorized` |
| 2 | 签名·签名不符 | 401 | 401 | 通过 | 错误码 `unauthorized` |
| 3 | 签名·时间戳越窗 | 401 | 401 | 通过 | 超出 ±5 分钟 |
| 4 | 签名·nonce 重放 | 401 | 401 | 通过 | 同一 nonce 第二次请求被拒 |
| 5 | `#01` 受理解析任务 | 202 | 202 | 通过 | `status=queued` |
| 6 | `#01` 幂等重试（同 job_id 同正文） | 202 | 202 | 通过 | 回首次受理回执，不重复建任务 |
| 7 | `#02` 轮询解析任务（到终态） | 200 | 200 | 通过 | `status=succeeded chunks=1 warnings=0` |
| 8 | `#02` 任务不存在 | 404 | 404 | 通过 | 错误码 `not_found` |
| 9 | `#03` 首次发布写入 | 201 | 201 | 通过 | `chunks=3 searchable=true` |
| 10 | `#03` 幂等重试（同 request_id 同正文） | 201 | 201 | 通过 | 回首次回执，不重复写入 |
| 11 | `#03` 同 publication_id 重复发布 | 409 | 409 | 通过 | POC 用 `idempotency_conflict`，契约未定义具体码（Q7） |
| 12 | `#07` 检索（`mode=keyword`） | 200 | 200 | 通过 | `hits=3`，`top_score=0.428571` |
| 13 | `#07` 检索（`mode=natural_language`） | 200 | 200 | 通过 | `hits=3`，`top_score=1.0`（RRF 归一化） |
| 14 | `#07` `authorization` 跨度超限（300s） | 400 | 400 | 通过 | POC 用 `invalid_request`，契约未定义（Q1） |
| 15 | `#14` `stream=true` 明确拒绝 | 400 | 400 | 通过 | 错误码 `unsupported_stream` |
| 16 | `#14` 生成回答 | 200 | 200 | 通过 | `citations=1`、`quotes_verbatim=true`、`no_evidence=false` |
| 17 | `#14` 幂等重试（同 request_id 同正文） | 200 | 200 | 通过 | `assistant_message_id` 与首次一致 |

补充断言（不止看 HTTP 码）：

- `#02` 的 `result.chunks` 非空且 `result.pages == []`（POC 无页图能力，口径见 Q12）；
- `#03` 的 `accepted_chunk_count == 3`、`searchable == true`、`revision == 101`（调用方传入值原样回执）；
- `#07` 的 `returned_count == len(hits)`、`no_evidence == false`，且命中项只含契约允许字段（不回 `content`）；
- `#14` 的每条 `citation.quote` 都能在**对应切片的完整正文**中逐字找到（`quotes_verbatim=true`）。

---

## 三、关键耗时初测（`poc_v1_metric`，本地回环 + 远程模型服务）

| 接口 | 样本数 | 端到端均值(ms) | 阶段均值(ms) |
| --- | --- | --- | --- |
| `#01/#02` parse | 1 | 688 | fetch 636 / parse 0 / chunk 0 |
| `#03` publish | 1 | 1488 | embed 1452 / persist 23 |
| `#07` search | 3 | 487 | embed 706(n=2) / keyword 7 / vector 13(n=2) / fuse 0(n=2) |
| `#14` chat | 1 | 16902 | embed 747 / keyword 5 / vector 9 / prompt 0 / llm 16110 |

读法说明：

1. **`#14` 的时间几乎全在模型侧**（llm 16110ms / 端到端 16902ms），检索与拼装合计 < 1s —— 优化空间不在本侧代码；
2. `search` 的 3 条样本里有 1 条是「nonce 重放」用例的首次合法请求（`mode=natural_language`，此时尚无发布数据，命中 0），故 `embed/vector/fuse` 标注 `(n=2)`：只有 2 条样本真的走了这些阶段；
3. `parse` 的 `fetch 636ms` 偏高（拉的是本机文件），需在 T2.x 用真实 GSI 文件地址复测，POC 数值不作容量依据；
4. 单条切片向量化约 **480ms**（publish 3 条 1452ms / 3），单次查询向量化约 **700ms**（含远程 ollama 网络往返）；
5. **同一 prompt 两次实测相差一倍**：`llm` 分别 16110ms 与 33688ms（端到端 16902ms / 34503ms）。以 60s 超时兜底时余量不足一半，见 `02` §三。

> 表格由 `mock_client --record` 从 `poc_v1_metric` 自动汇总，口径：按 `run_id` 只取本轮样本，每个阶段只对「真的走了该阶段」的样本求均值。

---

## 四、首轮失败与根因（8 条 → 0 条）

首轮真实冒烟（15:19，`--record` 前）跑出 9 通过 / 8 失败。三类缺陷全部属于**本侧实现问题**，与契约无关，已修复：

| 现象 | 根因 | 修复 |
| --- | --- | --- |
| `#01` 幂等重试 500（`duplicate key ... uq_poc_v1_parse_job_job_id`）、`#02` 永远 `queued` | FastAPI ≥0.106 的**依赖退出代码在响应之后执行**：后台解析任务读不到未提交的任务行，重试请求的 SELECT 也抢在首次提交之前 | `#01` 在 `background.add_task` 之前显式 `await db.commit()` |
| `#03` 500（`PocChunkModel() got multiple values for keyword argument 'publication_id'`） | 切片行构造与 `store.replace_chunks` 重复传 `publication_id` | 行数据去掉该字段，统一由 `replace_chunks` 注入 |
| 失败请求留下半成品：`#03` 500 后 publication 已落库，导致后续用例误得 409 | 异常被路由层转成错误响应后，依赖退出仍按「成功」提交 | 路由层置位 `request.state.poc_failed`，`poc_db` 按标记 `rollback()` |
| `#14` 幂等重试返回 409 `idempotency_conflict` | Mock 脚本每次重建 `authorization` 块，同 ID 重试的正文指纹变了 | 整组用例复用同一 `authorization`，`#07` 两种模式用不同 `request_id` |
| `#14` `citations=0` | 模型引用了不存在的编号（出现 `[9]`，而上下文只有 2 条）或干脆不标 | user 消息末尾追加「以上共 n 条资料，引用编号只能取 1–n」；越界编号只告警不编造 |

另修：耗时表曾混入历史轮次样本，导致出现「阶段均值 21700ms > 端到端 11688ms」的自相矛盾（已按 `run_id` 过滤 + 分阶段样本数标注）；`--reset` 与连接池释放（跨 `asyncio.run` 复用过连接会报 `Event loop is closed`）也在同一轮补齐。

---

## 五、单测（不依赖服务与数据库）

`backend/tests/test_poc_signature.py`：**17 passed**（约 1.3s）。

覆盖：签名原文 7 行拼装顺序、签名闭环、篡改正文失败、缺头、时间戳越窗、nonce 格式校验 / 重放 / 过期清扫、`authorization` 跨度与过期、`pick_quote` 结果必为正文逐字子串、`build_citations` 编号映射与越界忽略、`build_source_snapshots` 去重、`request_digest` 稳定性。

> 单测与冒烟互补：单测锁签名与引用的**纯函数口径**，冒烟锁**契约的 HTTP 行为**（状态码、错误码、幂等、字段形状）。
