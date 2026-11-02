# 服务大脑后端（backend）代码优化与简化建议报告

> 分析范围：`backend/app` 全部项目代码（排除 `.venv`）
> 分析维度：代码结构、模块划分、重复逻辑、性能、可维护性、正确性
> 分析日期：2026-11-02
> 变更记录：
> - v1.0（2026-11-02 初版）：基于含 `integration_chat` / `integration_kb` 模块的完整代码分析。
> - **v1.1（2026-11-02 复评）**：integration 两个模块已从代码库整体删除（见 `app/modules/` 现状），对报告做了全面复核——新增 **〇-A 复评章节**；已失效/已解除的条目在原文用【v1.1 复核】标注。注意：**删除不等于清理完成**，当前代码存在多处指向已删除模块的断裂 import，应用目前无法启动（见 〇-A.3）。
> 说明：所有问题均附代码位置（文件:行号）作为证据，按 **P0（正确性/数据安全，立即处理）→ P1（资源与稳定性，本迭代处理）→ P2（一致性/可维护性，排期处理）→ P3（清理项，顺手处理）** 分级。

---

## 〇、总体评价

架构意图清晰、工程化水平高于平均：分层骨架（controller → service → crud → model）基本一致、契约层有完整错误码表与原子 SQL、RAG 三闸置信与降级设计成熟、LLM/S3 阻塞调用基本都做了线程池隔离、注释质量好。

主要风险集中在三个"接缝"：

1. **基座模板残留与真实业务之间**——软删除失效、默认 500、大量死代码；
2. **迁移期双实现并存**——两套文档流水线、两套分片存储、两套会话表（**v1.1 复评：integration 模块删除后该项已基本解除，但产生了新的"删除残留断裂"风险，见 〇-A**）；
3. **核心层零测试**——`base_crud.py`（738 行核心）与业务 Service 层没有任何测试，上述问题均靠人工发现。

---

## 〇-A、【v1.1 复评】integration 模块删除后："迁移期双实现并存"风险核验

### A.1 三项双实现的核验结论

| 原风险 | 结论 | 说明 |
|---|---|---|
| **两套文档流水线**（`core/rag/pipeline.py` 通用版 vs `integration_kb/pipeline/document_pipeline.py` LangGraph 版） | **✅ 已解除，且发生反转** | LangGraph 版随模块删除。`core/rag/pipeline.py` 从"零调用的死代码"**反转为唯一实现**（契约 #01/#02 经 `contract/parse.py` 依赖它）。原报告 P2-1"删除 core 版"的建议作废；原 P1-3（双状态源）、P0-9（resume 重跑 embedding）随 LangGraph 版一起消失 |
| **两套分片存储**（`sbp_ai_kb_chunk` 自带 vector 列 vs `langchain_pg_embedding` PGVector collection） | **✅ 已解除，留数据清理尾巴** | 代码层现在唯一分片存储是 `sbp_ai_kb_chunk`；`core/rag/vector_store.py` 的 search/upsert 能力目前**零调用方**（仅 `validate_embedding` 被 `kb/publication/service.py` 使用）。存量数据库中的 `langchain_pg_embedding/_collection` 与 `sbp_ai_integration_kb_*` 各表**不会自动删除**，需清理迁移 |
| **两套会话表**（`kb/chat_session/chat_message`（uuid 主键，契约 #08–#13）vs `integration_chat/session`（int 主键）） | **✅ 已解除** | 现在唯一会话模型是 kb 域的 `sbp_ai_kb_chat_session/chat_message`；且契约 #08–#13 在 `contract/router.py` 本就是 skeleton，无实现冲突 |

**总体判断：三项"双实现并存"风险已全部解除，方向正确。** 但删除动作目前是"只删了模块目录"，配套收尾未做——产生了下面三类新问题，其中第一类是**阻断级**的。

### A.2 能力回退（有意删除带来的功能空缺，需确认是否有意为之）

删除的不只是"重复实现"，integration 模块承载的唯一实现能力也一并消失：

1. **问答全链路归零**：检索（`integration_kb/retrieval.py`）、重排调用、引用快照（`integration_chat/cite.py` 的 `build_cites/sanitize_citations`，被 `tests/test_chat_cite.py` 覆盖的逻辑）、SSE 流式问答、`ChatState` LangGraph 编排——全部随模块删除。契约 #07（检索）/#14（问答）本就是 skeleton，所以契约层不受影响，但**意味着服务大脑当前没有任何可用的问答/检索能力**，未来重建时只能以 kb 域为家。
2. **MinerU 解析器安装入口断裂**：`install_parser` 本体在 `core/rag/mineru.py:85` 仍在，但 lifespan 调用的是 `integration_kb.bootstrap.install_parser`（已删除）→ 二进制文档解析失去安装入口（修复：改 import 指向 core）。
3. **S3 能力失去归属**：`S3StorageClient` 原在 `integration_kb/clients/s3.py`，已删除。契约 #01 走 `file_url` 直拉（`contract/parse.py:124`）不经 S3，kb 域也无文件落 S3 的代码——若确认无文件存储需求，`S3_*` 配置与 lifespan 建桶逻辑应一并删除；若未来上传要落 S3，需在 core 或 kb 重建客户端。
4. **专用线程池删除**：`runtime/executor.py`（parse/io 分池）随模块删除，`core/rag/pipeline.py` 本就用的 `asyncio.to_thread`，口径反而统一了——但解析是大文档场景，将来若性能不达标需在 core 重建分池。

### A.3 【新增 P0】删除残留断裂清单（当前应用无法启动）

以下 import 指向已删除的 `app.modules.integration_*`，**模块级 import 断裂会让进程起不来**：

| 位置 | 断裂内容 | 影响 | 修复方式 |
|---|---|---|---|
| `app/api/v1/routers.py:12-19` | 8 个 integration controller import + `DOMAIN_CONTROLLERS` 中两个域 | **进程启动失败**（模块级 import） | 删除两组 import 与两个域的登记 |
| `app/bootstrap.py:18-19,26-27` | integration 两域 bootstrap import | **进程启动失败** | 删除两行 import 与 MODULE_BOOTSTRAPS 两项 |
| `app/contract/parse.py:28-30` | `ChunkedDocument/parse_and_chunk/PageLocator/supported_suffixes` 来自 integration_kb.pipeline | **契约 #01/#02 与进程启动失败** | 改指向 core：`parse_and_chunk/ChunkedDocument/supported_suffixes` ← `app.core.rag`；`PageLocator` ← `app.utils.page_locator`（core 版签名兼容，`ChunkedDocument.document.page_map` 字段同名） |
| `app/modules/kb/publication/service.py:21` | `validate_embedding` 来自 integration_kb | **进程启动失败，#03/#05/#06 全断** | 一行修复：改从 `app.core.rag.vector_store` 导入（`core/rag/vector_store.py:117` 有同名函数） |
| `app/__init__.py:21,55,64,95,113,121` | `ensure_vector_store/reset_interrupted_documents/install_parser/S3StorageClient/cancel_all/shutdown_*_executor` | lifespan 延迟 import，**启动期报错**（部分被 try/except 吞掉，部分直接抛） | 逐项处置：`install_parser` → `core.rag.mineru`；向量扩展初始化改由 core 提供（kb/bootstrap 注释明确"调用方须保证 vector 扩展就绪"）；worker/S3/线程池项直接删除对应 lifespan 段 |
| `app/scripts/initialize.py:35-37` | integration_kb bootstrap + space/category 模型 | 种子数据脚本失败 | 改为 kb 域表（`module_monitor/endpoint.js` 同步清理） |
| `tests/test_chat_cite.py:13-15` | `integration_chat.cite/rerank`、`integration_kb.retrieval.KnowledgeHit` | 测试套件失败 | `cite` 逻辑已随模块丢失：要么从 git 历史把 `build_cites/sanitize_citations` 迁到 `app/core/chat/cite.py` 保留测试，要么删除测试并记录"引用后处理能力待重建" |

配套收尾（P1/P3 级）：

- **存量数据清理迁移**（P1）：新增 alembic revision `DROP TABLE IF EXISTS langchain_pg_embedding/langchain_pg_collection/sbp_ai_integration_kb_*`；PGVector collection 数据如需保留另做导出。
- **孤儿配置清理**（P3）：`KB_RETRIEVE_TOP_K/KB_LLM_TIMEOUT` 已零引用；`VECTOR_AUTO_CREATE/VECTOR_DIM` 仅 `core/rag/vector_store.py` 使用（而该文件 search/upsert 已零调用）→ 若确认 #07 将用 `sbp_ai_kb_chunk` 自查，可整体下线 core/rag/vector_store 的 collection 机制，仅保留 `validate_embedding`。
- **checkpointer 空转**（P3）：`core/rag/checkpointer.py` 原只挂在已删除的 document_pipeline，现在 init/close 在 lifespan 空转 → 建议随 #14 重建时再启用，当前下线。
- **前端残留**（P1）：`frontend/src/api/index.js:9-18` 仍 re-export 已删除的 `module_integration_kb/module_integration_chat` 目录（**前端构建会失败**）；`module_monitor/endpoint.js:19-31` 探活仍打 `/integration-kb/*`、`/integration-chat/*`（监控页会全红）；`utils/sse.js` 注释引用已删除路径。

### A.4 受本变更影响的原报告条目对照

| 原条目 | v1.1 状态 |
|---|---|
| P0-8（searchable=True 不成立） | **仍成立且加剧**：现在连 integration 检索链路都没有了，#07 未上线前契约写入永远没有读路径 |
| P0-9（resume_from=WRITE 重跑 embedding） | 已失效（document_pipeline 已删除） |
| P1-1（客户端每请求重建） | **部分仍成立**：`EmbeddingClient` 在 `kb/publication/service.py` 仍每请求实例化；`LlmClient/RerankClient` 的调用方已删，问题随调用方消失但客户端单例化建议保留 |
| P1-2（SSE 读会话占连接池） | 已失效（问答链路已删） |
| P1-3（双状态源 + checkpoint 冗余） | 已失效；checkpointer 空转转为 A.3 收尾项 |
| P1-4（上传/下载全量入内存） | 已失效（document service 已删）；`contract/parse.py` 的 fetch_file 本就是流式 + 超限中断，口径良好 |
| P1-5（投递早于提交） | 已失效（integration_kb worker 已删）；契约 #01 的"先提交后 202"口径正确 |
| P1-6/P1-7（无 limit 查询/向量串行） | 已失效（涉及 integration_kb 的部分）；`base_schema` 时间字段、JSONB 索引等通用项仍成立 |
| P1-10（链路冻结 ADR） | **已解决**：删除即最彻底的冻结；后续以 kb 域为唯一知识链路（建议补一页 ADR 记录该决策与能力回退清单） |
| P2-1（删除 core/rag/pipeline 死代码） | **作废并反转**：core 版现为唯一流水线实现，须修复 `contract/parse.py` 的指向并补测试 |
| P2-2/P2-4 中引用 integration 模块的条目 | 已失效；其余（S3 对象键、状态映射收敛等若仍存在）以现状为准重新核对 |
| P2-3 分层收敛 | `kb/publication/service.py` 对 core 的反向依赖从"上移 validate_embedding"变为"改 import 路径"（更简单了），其余仍成立 |
| P0-1~P0-7、P0-10、P2-5、P2-6、P2-7、P3 | 均不受影响，维持原状 |

---

## 一、P0 —— 正确性与数据安全问题（立即处理）

### P0-1 契约路由吞异常，请求级事务"错误提交"（脏数据落库）

- 位置：`app/contract/errors.py:239`（写入 `request.state.contract_failed = True`）、`app/contract/deps.py:252`、`app/core/dependencies.py:42`
- 问题：`ContractApiRoute` 在路由内部把异常转换成 `Response` 返回，依赖栈看到的是"正常返回"，`db_getter` 的 `session.begin()` 正常提交。`contract_failed` 标记写入了但**没有任何代码读取它来做回滚**——失败请求的半成品数据会落库。
- 改法：`db_getter` 事务退出后显式检查并回滚：
  ```python
  async with async_session_factory() as session:
      try:
          async with session.begin():
              yield session
          if getattr(request.state, "contract_failed", False):
              await session.rollback()
      except Exception:
          await session.rollback()
          raise
  ```

### P0-2 幂等键可能"永久锁死"

- 位置：`app/contract/deps.py:248-262`（`_idempotent_guard`）
- 问题：幂等释放依赖 `contract_failed` 这个隐式契约。若依赖被挂到普通 `APIRouter`（不用 `ContractApiRoute`），异常直接上抛时 `release()` 不会执行，`request_id` 永久停在 `processing`，后续同 ID 重试全部 409。
- 改法：`_idempotent_guard` 用 `try: yield reservation; except: await release(...); raise` 双保险。

### P0-3 并发下 revision 单调性可被破坏（缺行锁/CAS）

- 位置：`app/modules/kb/publication/service.py:96-137`（`revise`）
- 问题：基线校验是"读→比→写"，无 `FOR UPDATE` 也无条件更新。两个并发请求都通过校验后写覆盖，revision 单调性被破坏，契约层的墓碑防复活机制失效。
- 改法：改为条件更新并检查 rowcount：
  ```python
  updated = await session.execute(
      update(KbPublicationModel)
      .where(id == publication.id, current_revision == base.revision)
      .values(current_revision=data.revision, ...)
  )
  if updated.rowcount != 1:
      raise EntryConflictError(...)
  ```

### P0-4 软删除基础设施整体失效（隐性 bug）

- 位置：`app/core/base_crud.py:70-72` 要求模型有 `is_deleted/deleted_time/deleted_id` 三字段，但 `app/core/base_model.py:82-123` 的 `ModelMixin` **没有 `deleted_id`**（全仓库确认不存在）。
- 后果：
  - `_supports_soft_delete` 恒为 False → `delete()` 永远物理删、`restore()` 永远抛异常；
  - 但 `is_deleted` 列真实存在且 `_build_conditions` 恒按其过滤，模块里还大量手写 `values(is_deleted=True)`——"删空间"用软删、"删文档"用物理删，同库两套删除语义；
  - 所有 `ix_*_status_deleted` 复合索引因 `is_deleted` 恒 false 退化为单列索引。
- 改法（二选一，不要维持现状）：给 `ModelMixin` 补 `deleted_id` 让软删真正生效；或明确"只物理删"，删除 `is_deleted/deleted_time`、`restore()` 与相关索引。

### P0-5 业务错误大面积返回 500

- 位置：`app/core/exceptions.py:41-55`，`CustomException` 默认 `status_code=500`。
- 后果：`get_or_404` docstring 承诺 404 实际返回 500；几十处 `raise CustomException(msg="...不存在")` 全部变 500，监控无法区分真实故障与业务拒绝；且 `msg` 内嵌 `{e!s}` 会把 SQL 细节回显给客户端。
- 改法：默认改 400（或强制显式传状态码）；`get_or_404` 内部固定 404；prod 下对 msg 白名单化。

### P0-6 数据库明文口令硬编码进源码

- 位置：`app/config/setting.py:89`，`DATABASE_PASSWORD: str = Field(default="Yidatec@2026!")`。
- 改法：默认值改空串 + 启动期非空校验；**立即轮换该口令**并考虑从 git 历史清除。

### P0-7 Alembic baseline 在空库上无法执行

- 位置：`app/alembic/versions/20261029_5d5210a8fd5e_*.py:495-502`
- 问题：迁移对 `sbp_ai_system_api_log` 做 `add_column`，但该表在迁移里从未 `create_table`（假设基座 `create_all` 先建好）。空库跑 `upgrade head` 报 `UndefinedTable`；若启动建表先执行，则 `add_column` 报 `DuplicateColumn`——**任何顺序都失败**，只能靠人工 `stamp` 掩盖。同时启动期 `create_missing_tables` 与 Alembic 三套 DDL 并存，"只建缺失表不补列"，新列场景下审计日志会被 `try/except` 静默丢弃（`core/router_class.py:150-155`）。
- 改法：
  - baseline 拆为可独立 review 的按域 revision，`0001` 全量 `create_table`（含 api_log 完整目标结构），去掉对旧表的 `add_column/drop_index`；
  - 生产唯一事实源 = Alembic，`create_missing_tables` 仅 dev/test 生效；
  - 启动时校验 `alembic_version == head`，不等则拒绝启动；
  - CI 增加 `alembic upgrade head && downgrade -1 && upgrade head` 回归。

### P0-8 契约链路"写入即可检索"承诺不成立【v1.1 复核：仍成立且加剧】

- 位置：`app/modules/kb/publication/service.py:215-248` 写向量到 `sbp_ai_kb_chunk`，但全仓库**没有任何读路径**——原先问答检索走的 `integration_kb/retrieval.py` 已随模块删除，#07 仍是 skeleton。
- 改法：#07 上线前不标记 `searchable`；或补读路径 + e2e 测试断言"写入即可召回"。

### P0-9 `resume_from=WRITE` 仍重跑 embedding【v1.1 复核：已失效】

- 原问题位于已删除的 `integration_kb/pipeline/document_pipeline.py`，随模块删除消失。

### P0-10 契约必填字段无落点，多租户/审计链路断裂

- 位置：`EntryWriteData` 携带 `tenant_id/user_id/version_id/knowledge_no/metadata`，写库时全部丢弃；`sbp_ai_kb_chunk` 没有 `chunk_id` 列（#04 无法还原 GSI 的 chunk_id）。`sbp_ai_kb_publication` 不存 `resource_id/tenant_id`，归属校验只能比 `resource_type`——**任何人拿到 publication_id 即可改写他人知识**（`publication/service.py:203-213` 注释自认）。
- 改法：`publication` 补 `resource_id/tenant_id`，`chunk` 补 `chunk_id`（`UNIQUE(record_id, chunk_id)`），`revision` 补 `version_id/knowledge_no/metadata`。这是契约 #04/#05/#06 闭环的前提。

---

## 二、P1 —— 资源、性能与稳定性（本迭代处理）

### P1-1 LLM/Embedding/Rerank 客户端每请求重建，httpx 连接从不关闭【v1.1 复核：部分仍成立】

- 原位置：`integration_chat/retriever.py:36`、`core/chat/rerank.py:62`、`integration_chat/pipeline.py:332` 等（多数调用方已删）。
- 现状：`kb/publication/service.py` 仍每请求实例化 `EmbeddingClient`，底层 `httpx.AsyncClient` 不关闭的问题（`core/clients/llm.py:114`、`base.py:108`）仍在客户端封装内。
- 改法不变：`app/core/clients/` 提供进程级单例（`get_embedding_client()` / 挂 `app.state`），生命周期跟随应用，关停时统一 `aclose()`——#14 问答链路重建时强制遵守。

### P1-2 SSE 读会话贯穿整个流生命周期，占满连接池【v1.1 复核：已失效】

- 原问题位于已删除的 `integration_chat` 问答链路；规范条目 F3/L15 保留，作为 #14 重建时的强制要求。

### P1-3 文档流水线双状态源 + 全量 state 落 checkpoint【v1.1 复核：已失效，衍生新清理项】

- 原问题（AsyncPostgresSaver + S3 bundle 双状态源）随 `document_pipeline.py` 删除而消失。
- 衍生项：`core/rag/checkpointer.py` 现在零消费者，lifespan 中 init/close 空转（见 〇-A.3 收尾清单）——建议下线，待 #14 重建时再启用。

### P1-4 上传/下载/解析三处全量入内存【v1.1 复核：已失效】

- 原问题（`document/service.py` 上传 1GB 峰值、下载 zip 全量、S3 三次拷贝）随 `integration_kb` 删除消失。`contract/parse.py:124` 的 `fetch_file` 本就是流式拉取 + 超限即时中断，口径良好——重建上传能力时以它为范本。

### P1-5 任务投递早于事务提交，靠轮询兜底【v1.1 复核：已失效】

- 原问题（integration_kb worker 轮询 + 拿错任务 id）随模块删除消失。契约 #01（`contract/parse.py`）的"202 前先提交任务行"口径正确，无此问题。

### P1-6 无 limit 查询与无索引的 JSONB 过滤【v1.1 复核：integration 相关项已失效，通用项仍成立】

- 已失效：`task/crud.get_latest_by_doc`、`document/service.py:588`（integration_kb 已删）。
- 仍成立：`core/rag/vector_store.py:228-232` 的 `cmetadata` 表达式过滤无 GIN 索引（该模块现状见 〇-A.3 收尾项）；`system/api_log.security_check` 同理（access_trace 概览页 5 次全表扫）；`nonce.request_id`、`idempotency.biz_id`、`request_log.expires_at` 均为高频查询字段但无索引。
- 仍成立：`base_schema.py` 的 `BaseQueryParam` 强制继承 `updated_time`，但 6 张台账表（nonce/idempotency/request_log/auth_cache/parse_result/eval_case）没有该列——前端传时间范围即触发 500。`base_crud._build_conditions` 需加 `hasattr` 兜底。

### P1-7 向量化串行分批 + 无显式重试【v1.1 复核：仍成立（写入链路）】

- 原位置：`core/clients/embedding.py:123-126`——300 分片 × batch16 = 19 次串行 RTT；`OpenAIEmbeddings` 未配置 `max_retries`。
- 现状：`kb/publication/service.py` 的写入链路（#03/#05）仍使用该客户端批量向量化，中途失败即整单失败的问题不变。
- 改法：`asyncio.gather + Semaphore(3)` 并发分批；显式重试 + 失败可重入。

### P1-8 request_body 被解析 4 次、审计解出上传文件

- 位置：`contract/deps.py:140`、`contract/router.py:103`、`contract/deps.py:235`、`contract/errors.py:258` 各自 `json.loads` 一次；`core/router_class.py:174-195` 审计对 multipart 调 `request.form()` 解出文件内容，计入接口耗时且造成内存峰值。
- 改法：`verified_signature` 解析一次存 `request.state.contract_json`，后续依赖复用；上传路由跳过 body 采集（只记文件名/数量），JSON 采集加长度截断。

### P1-9 契约验收脚本断言永不成立的错误码

- 位置：`contract/mock_gsi.py:96-101,461` 断言 `UNSUPPORTED_STREAM`，但 `contract/errors.py` 中**不存在**该码。另 `verify_m1.py` 与 `mock_gsi.py` 重复实现 `_json/SKELETON_STATUS/错误码常量`。
- 改法：抽 `app/contract/testing.py` 共用；`mock_gsi` 错误码改为 `from .errors import`；补齐或修正 `UNSUPPORTED_STREAM`。

### P1-10 决策：两条知识链路哪条是事实标准（冻结另一条）【v1.1 复核：已解决】

- 原问题：`app/modules/kb/`（/v1 契约链路，8 张表）与 `app/modules/integration_kb/`（/api/v1 管理端）是**两条互不相通的完整流水线**（不同任务表、不同状态机、两套向量存储）。同样，`kb/chat_session/chat_message` 与 `integration_chat/session` 两套会话表冲突建模。
- **现状：integration 模块已整体删除，kb 域成为唯一知识链路——以最彻底的方式落地了"冻结"**。建议补一页 ADR 记录：删除决策、随删除回退的能力清单（检索/问答/引用/任务编排，见 〇-A.2）、未来重建以 kb 域为家的约定。
- 遗留：存量数据库中 integration 侧表需清理迁移（见 〇-A.3）。

---

## 三、P2 —— 一致性与可维护性（排期处理）

### P2-1 删除迁移期死代码与兼容层（约 1000+ 行）

| 死代码 | 位置 | 说明 |
|---|---|---|
| 旧版文档流水线 | ~~`core/rag/pipeline.py` 的 `parse_and_chunk/process_document/embed_chunks`~~ | **【v1.1 复核：作废并反转】** integration 版已删，core 版现在是唯一流水线实现（`contract/parse.py` 依赖它）。应做的不是删除，而是：修复 `contract/parse.py:28-30` 的断裂 import 指向 core、清理 `core/rag/__init__.py` 的 `__all__` 导出、补齐解析/分片的最小测试。`build_embedding_rows` 继续保留 |
| 6 个纯转发兼容层 | ~~`integration_kb/pipeline/{chunker,...}.py` 等~~ | **已随 integration 模块删除消失**（转发层目标本体被删，注意 `contract/parse.py` 仍在 import 其中三个——见 〇-A.3） |
| 基座残留 | `common/constant.py`（GenConstant 488 行代码生成器常量）、`common/dataclasses.py` 全部 10 个类、`common/enums.py` 多个枚举、`validator.py:223-294`、`core/dependencies.py:13-39`（教程残留）、`exceptions.py` 的 `require_superadmin`、`response.py:104-238` 零增值包装 | 已 grep 验证零引用，约占 `common/` 一半 |
| 空壳 CRUD × 11 | `integration_kb/{category,space}`、`kb/*` 7 个、`system/{auth_cache,prompt_template,request_log}` | **【v1.1 复核】** integration 侧 2 个已随模块删除；`kb/*` 7 个与 system 侧 3 个仍在（每个只有"绑定 model"4 行；`kb/*` 泛型参数写成 `BaseModel, BaseModel` 完全无意义） |
| 过期 schema 副本 | `system/api_log/schema.py` 的 `SystemApiLogCreateSchema` | 缺 4 个新列，是"假事实源" |
| 未接入的提示词表 | `system/prompt_template/` | **【v1.1 复核】** 原硬编码 prompt 的 `integration_chat/prompt.py` 已删——现在项目里**没有任何 prompt 实现**，该表问题从"两套并存"变为"表空置"。#14 重建时二选一：接入表读取（带缓存），或删表并在 ADR 记录"prompt 随代码版本管理" |

### P2-2 抽取重复逻辑为单一来源【v1.1 复核：涉及 integration 的行已失效】

| 重复逻辑 | 出现位置（份数） | 收编方向 |
|---|---|---|
| S3 对象键拼接 / 资产清理 / id→name 映射 | 原 `document/space/retrieval` 共 8 份 | **已随 integration 删除失效**（S3 能力归属待定，见 〇-A.2；重建时按单一来源原则一次做对） |
| 排序白名单 | 原 `document/service.py:611` 已删；`base_crud._parse_order`、`vector_store._SORTABLE_FIELDS` 2 份仍在 | 统一走 `PaginationQueryParam` 校验 |
| 状态/文案映射 | 原 4 处（integration 侧）已删 | 重建任务/流水线状态时按"枚举自带 label"一次做对 |
| 幂等薄壳 | `contract/idempotency.py` 的 `Reservation` 与 `system/idempotency/service.py` 的 `IdempotencyOutcome` 语义重复 | 合并为别名/子类；`NonceStore` 单例降为函数 |

### P2-3 分层口径收敛【v1.1 复核：integration 相关项已失效】

- 已失效：原 service 裸 SQL 10+ 处（space/category/session/stats）、`conversation.py` 双写路径、消息 Schema 双份定义（均在已删模块）。重建时按规范 F1/F11 一次做对。
- core 层反向依赖业务层：`core/router_class.py:148` import `system/api_log/model` 仍在；`kb/publication/service.py:21` 对 `integration_kb/chunk/vector_store` 的反向依赖已随删除变为**断裂 import**——按 〇-A.3 改从 `core/rag/vector_store` 导入即一并解决。
- `CRUDBase` 的 18 处 `except Exception: raise CustomException(msg=f"...: {e!s}")` 吞掉 `IntegrityError`（应映射 409），全局 `sqlalchemy_exception_handler` 永远接不到 → CRUD 不捕异常或只记日志，交全局 handler 按类型映射。

### P2-4 LangGraph 使用规范化【v1.1 复核：全部失效】

- 原五个问题（ChatState 混入依赖、图重编译、进度三表并存、失败定位、无重试）全部位于已删除的 integration 图编排中，随删除消失。规范第 4.3/4.4 节整体保留，作为 #07/#14 检索与问答链路重建时的强制要求（重建时以 kb 域为家，不再新建并列模块）。

### P2-5 拆分超长代码

| 位置 | 规模 | 建议 |
|---|---|---|
| `core/base_crud.py` | 738 行 | 软删决策后拆分；create/update 尾部两段相同"重查审计字段"块抽 `_reload_with_audit()` |
| ~~`integration_kb/document/service.py`（633 行）~~ | 已删除 | — |
| ~~`pipeline/document_pipeline.py`（623 行）~~ | 已删除 | — |
| ~~`integration_chat/pipeline.py`（594 行）~~ | 已删除 | — |
| `app/__init__.py lifespan` | 122 行、8 个同构 try/except（**v1.1：其中 5 段引用已删模块，见 〇-A.3**） | 先修断裂，再抽 `(name, coroutine_factory)` 列表统一执行 |
| `contract/deps.verified_signature` | 129 行 | 拆 OpenAPI 声明/审计回填/验签/nonce 登记四步 |

### P2-6 配置管理收敛

- `_DB_DRIVERS`（`setting.py:245`）与 `_ASYNC_DRIVER_MAP`（`common/enums.py:55`）完全重复 → 留一份。
- `AUTOCOMMIT/AUTOFLUSH/AUTOFETCH` 三项中 `AUTOFETCH` 语义不明却优先级最高（`database.py:54` 疑似笔误产物）→ 清理。
- 292 行单类 9 大域 100+ 配置 → 按 pydantic-settings 子模型拆分嵌套。
- `RET` 错误码成功 0/200/201 混用、错误 1/-1/4003~4506 三套段位并存 → 收敛一套。
- 数据库连接失败返回 403（`exceptions.py:119`，显然想要 503）→ 修正。
- 事件循环工厂 `core/loops.py` 名不副实（实为 Windows 事件循环选择器）→ 改名 `event_loops.py`。

### P2-7 数据库设计规范落地（详见规范文档，此处列存量问题）

- 时间字段 `_time/_at` 混用且 `BaseQueryParam` 只识别 `_time`；
- `status` 一半 int 一半 varchar，全部无 CHECK 约束；
- 默认值全在 Python 侧无 `server_default`（裸 SQL/ETL 必踩 NOT NULL）；
- 外键三种口径并存（物理外键/裸 String/裸 int），kb 域删除时 revision/chunk 孤儿数据无清理机制；
- 未设 `naming_convention`，`uq_` 前缀误用于 `Index(unique=True)`（`ON CONFLICT` 语义陷阱）；
- 向量维度 `Vector(settings.VECTOR_DIM)` 与迁移硬编码 1024 会漂移（kb chunk 表沿用此口径，仍成立）；
- ~~`space.doc_count` 冗余列~~（integration 侧，已随删除失效）→ 重建统计能力时改 JOIN 聚合；
- MySQL 兼容分支只做了一半（api_log 有 LONGTEXT 分支，其余全用 JSONB/pgvector）→ 文档明确"仅支持 PostgreSQL 16 + pgvector"。

---

## 四、P3 —— 清理与测试补强【v1.1 复核：随删除调整】

1. **测试缺口**（当前 6 个测试文件集中在 /v1 契约层）：
   - `base_crud.py`（738 行核心）零测试——P0-4/P0-5 这类问题一个最小用例即可暴露；
   - 业务 Service 层零测试（kb 写入链路的去重/冲突/孤儿清理语义）；
   - ~~`integration_chat/pipeline.py` 零测试~~（已随删除失效；#14 重建时帧序契约/拒答/故障路径必须带测试落地）；
   - **新增：`tests/test_chat_cite.py` 已断裂**（import 已删模块）——引用后处理逻辑（`build_cites/sanitize_citations`）只存在于 git 历史，决定迁移还是放弃（见 〇-A.3）；
   - `/api/v1` 侧分页/排序/统一响应无端到端验证；
   - DB 类用例在数据库不可达时自动跳过——CI 不配 DB 则"永远绿"；`tests/debug_body.json` 调试残留。
2. ~~上传响应 `documents/failed` 双列表无顺序信息~~（已随删除失效）。
3. ~~`top_k` 默认值三处不一致~~（调用方已删，仅剩 settings 与 core 默认值——#07 重建时按"基础设施层默认 None"执行）。
4. `streaming.py` 用标准库 logger 与全项目 loguru 口径不一致。
5. `vector_store` 全局惰性初始化无锁（现状见 〇-A.3 收尾项——若该模块整体下线则随之消解）。
6. 文档漂移：`main.py` 注释端口 8002 vs `PORT` 默认 8000；voice 模块注释"23 张表"实建 8 张。

---

## 五、建议执行批次【v1.1 修订】

| 批次 | 内容 | 关键词 |
|---|---|---|
| **第 0 批（立即）** | **修复删除残留**（〇-A.3）：routers/bootstrap/contract-parse/publication/initialize/test 七处断裂 import、lifespan 五段失效初始化、前端 api/index.js 断裂 re-export | 应用恢复可启动、契约 #01–#06 恢复可用 |
| 第 1 批（本周） | P0-1/2/3/4/5/6 | 事务回滚、幂等释放、CAS、软删决策、默认状态码、口令轮换 |
| 第 2 批（本迭代） | P0-7/8/10 + P1-1/6/7 + 存量表清理迁移 | 迁移重构、searchable 语义、契约字段落点、客户端单例、索引补齐、DROP integration 侧表 |
| 第 3 批（下迭代） | P1-8/9 + checkpointer/vector_store 下线决策 + ADR（删除决策与能力回退清单） | body 解析去重、验收脚本共用、基础设施瘦身、决策留痕 |
| 第 4 批（持续） | P2 剩余 + P3 | 死代码清理、分层收敛、测试补强（每 PR 顺手处理 P3 项） |

配套动作：第 0 批修复时同步把 `core/rag/pipeline` 的解析/分片最小测试补上（它从死代码变成了唯一实现，却没有测试兜底）；#07/#14 重建前先读规范第 4 章。
