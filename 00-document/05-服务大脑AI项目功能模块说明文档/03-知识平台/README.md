# 03 · 知识平台

> **一句话定位**：GSI 交付的文档与 FAQ 在这里解析成切片、落成发布单元，再被检索、问答与重复治理复用。
> 归属域 `kb`（九个一表一目录的子模块）· 数据表 9 张 · 接口 48 个（契约 `/v1` 20 + 管理端 `/api/v1/kb` 28）· 前端页面 6 个 · 版本 v1.0（2026-11-03）

---

## 一、边界

**这个模块做**

- 接收 GSI 的解析任务：按 `job_id` 幂等受理，同步校验文件（类型白名单、10MB 上限），后台解析出**无向量的切片产物**（`chunks` / `pages` / `suggested` / `warnings`），四态进度可查。
- 保存知识：只有 GSI 编辑人员审批通过的切片才会写入本侧（03.1 新增 / 03.3 修订 / 03.4 删除 / #20 停用），写入时同步向量化，并做「全量替换 + 指针切换 + 版本号单调」。
- 检索：按签名身份的授权范围先下推再召回，关键词与自然语言两条通道共用 RRF 融合与精排闸门，返回 chunk 级命中与相对分。
- 问答：检索 → 拼上下文 → 调 Chat 模型 → 落会话与消息（含引用九字段与来源六元组），支持非流式（契约）与 SSE 流式（管理端）两种出口。
- 重复治理：保存前查重（只看不写）、重复分组查询/详情、合并预览（只产草稿态），并把治理结论物化成 `dup_group` 行供管理台跟踪。
- 管理台维护：发布单元/版本/切片的 CRUD、解析任务查询、重复分组状态标记、检索调试台与通道配置、会话与消息浏览。

**这个模块不做**

- 不做知识录入界面与审批流：切片由 GSI 编辑人员确认后才下发，本侧没有「草稿知识」表，`dup_result` 里的草稿只是检测时随请求带进来的内存快照。
- 不解析 Office/图片的页图：页图预览只对 PDF 生效，其余类型返回 `pages=[]`，不伪造图片 URL。
- 不做流式契约：`/v1/chat/completions` 收到 `stream=true` 直接 400 `UNSUPPORTED_STREAM`，SSE 只在管理端 `/kb/chat/ask/stream` 提供。
- 不实现 v3 手册新增的 **D06 单篇知识切片查重**（`chunk-check`）：代码里没有该路由，前端也没有入口，属未实现能力（见 `04-实现与运维说明.md` §八）。
- 不做多租户隔离的库级约束：`tenant_id` 是业务列，隔离靠每次查询显式带条件，没有 RLS。

---

## 二、能力地图

按使用者视角分组，代码上分别落在 `kb` 域九个自模块与 `modules_api/kb` 四个能力包（协议基座在 `modules_api/base`）：

| 能力 | 使用者 | 入口 | 涉及表 |
| --- | --- | --- | --- |
| 提交解析任务、看进度与产物 | GSI 后端（契约） | `/v1/parse/jobs` | `parse_job` / `parse_result` |
| 写入审批通过的切片（新增/修订/删除/停用） | GSI 编辑人员（契约） | `/v1/knowledge/entries*` | `publication` / `revision` / `chunk` |
| 读回已批准基线（03.2） | GSI 编辑人员、管理台 | `/v1/knowledge/entries/{id}/query`、`/kb/publication/baseline/{id}` | `revision` / `chunk` |
| 关键词 / 自然语言检索 | GSI 座席（契约）、联调（管理台） | `/v1/knowledge/search`、`/kb/retrieval/search` | `chunk` / `publication` + GSI 授权表 |
| 检索通道参数只读查看 | 运维、算法 | `/kb/retrieval/config` | 无（读环境变量） |
| 多轮问答（非流式） | GSI 座席（契约） | `/v1/chat/completions` | `chat_session` / `chat_message` |
| 多轮问答（SSE 流式） | 管理台调试 | `/kb/chat/ask/stream` | `chat_session` / `chat_message` |
| 会话增删改查、清空历史 | GSI 座席（契约）、管理台 | `/v1/chat/sessions*`、`/kb/chat-session/*` | `chat_session` / `chat_message` |
| 超时请求的结果查询 | GSI 座席（契约） | `/v1/chat/requests/{id}/query` | `sbp_ai_system_request_log`（跨模块） |
| 保存前查重 | GSI 编辑人员（契约） | `/v1/duplicates/check` | 读 `chunk` / `publication`，不写表 |
| 重复分组清单与详情 | GSI 编辑人员（契约） | `/v1/duplicates/groups*` | `dup_result` / `dup_group` |
| 合并预览 | GSI 编辑人员（契约） | `/v1/duplicates/merge-preview` | `dup_result` / `dup_group` |
| 治理结论跟踪与状态标记 | 管理台 | `/kb/dup-groups/*` | `dup_group` |
| 发布单元 / 版本 / 切片的维护浏览 | 管理台 | `/kb/publication/*` | 三张内容表 |

---

## 三、表速查

| 表名 | 中文名 | 角色 | 写入方 | 保留策略 |
| --- | --- | --- | --- | --- |
| `sbp_ai_kb_publication` | 发布单元 | 台账（状态机） | `kb.publication.service.KbEntryService.create/revise/delete/deactivate`、`KbPublicationService.create/update/update_status` | 永久；契约删除只置 `status='deleted'`，管理端整单删除物理删 |
| `sbp_ai_kb_revision` | 版本快照 | 台账（墓碑） | `KbEntryService.revise/delete/deactivate` | 永久；删除留 `status='archived'` 墓碑行（`content_hash` 为空） |
| `sbp_ai_kb_chunk` | 知识切片 | 明细（含向量） | `KbEntryService.create/revise`（向量先算后写）、`delete`（物理删） | 永久；删除即物理删，停用归档为 `archived` |
| `sbp_ai_kb_parse_job` | 解析任务 | 台账（状态机） | `kb.parse.service.create_parse_job_record` → `KbParseJobService.create_job/start/complete/fail` | 永久，无清理任务 |
| `sbp_ai_kb_parse_result` | 解析产物 | 明细（不可变） | `KbParseJobService.complete`（与任务状态同事务） | 设计 90 天，**无清理实现** |
| `sbp_ai_kb_dup_result` | 重复检测结果快照 | 缓存（30 分钟） | `kb.dup_result.service._store`（D02/D03 首查） | 30 分钟（`expires_at`）；过期只判不删 |
| `sbp_ai_kb_dup_group` | 重复分组 | 台账（治理物化） | `kb.dup_result.service._materialize`、`_save_preview`、管理端 `KbDupGroupService.update_status` | 永久 |
| `sbp_ai_kb_chat_session` | 问答会话 | 台账（软删） | `KbChatSessionService.create_owned/_clear/_delete`、`update` | 永久；删除与清空都是改状态，行保留 |
| `sbp_ai_kb_chat_message` | 会话消息 | 明细（软删） | `kb.chat.service.KbChatService._persist`、`KbChatMessageCRUD.mark_session_deleted` | 永久；清空/删除只置 `status='deleted'` |

字段、键、索引与表间引用见 [`02-数据模型与表关系.md`](02-数据模型与表关系.md)；保留策略的完整口径见 [`01-业务功能说明.md`](01-业务功能说明.md) §六。

---

## 四、接口速查

两组前缀，鉴权方式完全不同（详细约定见 [`03-接口清单.md`](03-接口清单.md) §一）：

- 契约侧 `/v1`：HMAC 五头 + 裸 JSON（不套 `code/msg/data`），20 个端点，写操作走幂等台账；
- 管理端 `/api/v1/kb`：管理台 JWT + 统一 `ResponseSchema`，28 个端点。

| 组 | 方法 | 路径 | 用途 | 数量 |
| --- | --- | --- | --- | --- |
| 解析 | POST / GET | `/v1/parse/jobs`、`/v1/parse/jobs/{job_id}` | #01 受理、#02 查进度与产物 | 2 |
| 知识写入 | POST / PUT / DELETE | `/v1/knowledge/entries`、`/entries/{publication_id}`、`/entries/{publication_id}/query`、`/entries/{publication_id}/deactivate` | 03.1 新增、03.2 读基线、03.3 修订、03.4 删除、#20 停用 | 5 |
| 检索 | POST | `/v1/knowledge/search` | #07 关键词 / 自然语言检索 | 1 |
| 会话 | POST / PATCH / DELETE | `/v1/chat/sessions`、`/sessions/query`、`/sessions/{id}/query`、`/sessions/{id}`、`/sessions/{id}/messages` | #08–#13 六操作 | 6 |
| 生成 | POST | `/v1/chat/completions`、`/chat/requests/{target_request_id}/query` | #14 生成、#15 超时结果查询 | 2 |
| 重复治理 | POST | `/v1/duplicates/check`、`/groups/query`、`/groups/{group_id}/query`、`/merge-preview` | #16–#19 | 4 |
| 发布单元 | GET/POST/PUT/PATCH/DELETE | `/api/v1/kb/publication/*` | 列表/详情/增改/状态/删除/版本/切片/基线/删快照 | 12 |
| 解析任务 | GET | `/api/v1/kb/parse-jobs/list`、`/detail/{job_id}` | 任务列表与详情（含产物） | 2 |
| 重复分组 | GET / PATCH | `/api/v1/kb/dup-groups/list`、`/detail/{group_id}`、`/status/{group_id}` | 治理结论浏览与状态标记 | 3 |
| 检索 | GET / POST | `/api/v1/kb/retrieval/config`、`/search` | 通道配置、检索调试 | 2 |
| 会话 | GET / PUT / DELETE | `/api/v1/kb/chat-session/*` | 列表/详情/改名/清空/删除 | 5 |
| 消息 | GET | `/api/v1/kb/chat-message/list`、`/detail/{message_id}` | 历史消息浏览 | 2 |
| 对话 | POST | `/api/v1/kb/chat/ask`、`/chat/ask/stream` | 非流式问答、SSE 流式问答 | 2 |

---

## 五、代码地图

| 路径 | 职责 | 备注 |
| --- | --- | --- |
| `app/modules/kb/publication/service.py` | 两个服务类：`KbEntryService`（契约写入）+ `KbPublicationService`（管理端 12 个动作） | 840 行，写入链路的核心；向量化在事务之前 |
| `app/modules/kb/retrieval/service.py` | 授权下推、关键词打分、向量召回、RRF 融合、精排闸门 | 335 行；`authorized_publication_ids` 被契约/管理端/问答三处共用 |
| `app/modules/kb/chat/service.py` + `sse.py` | 问答编排（检索→生成→落库）与 SSE 帧协议 | `sse.py` 只有事件定义，编排在 `service.py` |
| `app/modules/kb/chat/admin_service.py` | 管理端两个出口：`ask` 与 `open_stream` | 自己开会话（无请求级事务），首轮自动建会话 |
| `app/modules/kb/dup_result/service.py` | 检测、快照存取、物化治理行、合并预览 | 常量在 `dup_result/schema.py`（30 分钟、1000 项、0.65 阈值） |
| `app/modules/kb/parse_job/service.py` + `parse_result/service.py` | 任务四态与产物落库 | `reset_unfinished` 在启动期把未完成任务改判 `failed` |
| `app/modules/kb/chat_session/service.py` + `chat_message/` | 会话与消息的读写、软删 | 管理端不校验归属，契约按 `user_id` 收窄 |
| `app/modules_api/kb/{parse,knowledge,duplicate,chat}/` | 契约 20 个端点：controller（薄）+ service（编排）+ schema（字段约束） | controller 只做「回放→调用→登记回执」 |
| `app/modules_api/base/` | 契约基座：`deps.py`（验签/nonce/幂等）、`errors.py`（错误码+路由类）、`signature.py`、`audit.py`、`common.py`（`make_contract_router`）、`test_utils/` | 新增对外接口直接复用，业务包不复制协议口径 |
| `app/modules_api/base/deps.py` | 五头验签、nonce 防重放、三类幂等预约、授权时效校验 | `verified_signature` 必须在 Pydantic 之前拿到原始 body |
| `app/modules_api/base/errors.py` | 错误码常量、码→HTTP 状态映射、`ContractApiRoute` | 所有契约错误码的唯一来源 |
| `app/api/v1/routers.py` + `app/modules_api/router.py` | 两组前缀的挂载点 | `/api/v1` 下 7 个管理端 Router；`/v1` 下 4 个契约 Router |
| `app/__init__.py`（lifespan） | 建扩展/建表/补关键词索引/改判未完成解析任务/确保对象存储桶 | 页图改由对象存储预签名 URL 提供，本地 `/previews/parse` 路由已下线 |
| `frontend/src/views/module_kb/`、`api/module_kb/` | 6 个页面 + 6 个接口封装 | 页面不做本地过滤，全部下推后端 |

---

## 六、术语

| 术语 | 含义 |
| --- | --- |
| 发布单元（publication） | 一条知识的容器，`publication_id` 由 GSI 提供；本侧用它做授权与状态的最小单位 |
| 记录（record_id） | 一次「审批通过的内容快照」的身份，等于 GSI 的 `sbp_km_doc_record.id`；检索命中的最小可回查单位 |
| 版本（revision） | 发布单元内单调递增的版本号；`(publication_id, revision)` 唯一，修订必须严格大于当前值 |
| 基线（baseline） | 当前生效快照的完整切片集合；03.2 就是「读基线」 |
| 墓碑行 | `revision` 里 `status='archived'` 且 `content_hash` 为空的行，用于拒绝旧 revision 重复提交并可被删事件查到 |
| 切片（chunk） | 知识的最小检索单元；`uuid` 与 `chunk_id` 的关系见 `02-数据模型与表关系.md` §3.3 |
| `chunk_id` 归属 | v3 起切片 ID 由 AI 分配（`record_id:seq`），GSI 只引用；请求里可省略 |
| 授权下推 | 先算「这个用户能看到哪些 `publication_id`」，再在 SQL 里带 `publication_id IN (...)`，不是查完再过滤 |
| 精排闸门 | 两层：绝对下限 `KB_RERANK_MIN_SCORE` 与相对下限「top1 分数 × `KB_RERANK_RELATIVE_RATIO`」，取两者较大值 |
| 拒绝回答（no_evidence） | 召回条数低于 `KB_GRADE_MIN_CHUNKS` 时直接拒答，`finish_reason=no_evidence`，不编答案 |
| 知识依赖（knowledge_deps） | 一条回答用到的 `publication_id + revision`；任意一项失效，该回答整条不再展示 |
| 迟到丢弃 | 生成过程中会话被删除/清空，写回前用行锁 + 状态快照比对，判定丢弃并回 `TURN_DISCARDED` |
| 治理快照 | `dup_result` 的 30 分钟只读结果；`scope_digest` 排除 `scope_id` 与时间戳，翻页时重新签发的 scope 仍能对齐 |
| `group_key` | 重复分组的稳定标识（类型 + 排序后成员身份的摘要），也是契约里的 `group_id`；不等于行 `uuid` |

---

## 七、文档导航

| 文档 | 回答 |
| --- | --- |
| [`01-业务功能说明.md`](01-业务功能说明.md) | 谁在什么场景用什么功能；解析/写入/检索/问答/治理五条主流程；状态如何判定；数据留多久；现在做不到什么 |
| [`02-数据模型与表关系.md`](02-数据模型与表关系.md) | 九张表每列的含义；键与索引；表与表怎么对上（R1–R10）；谁写谁读；删除一条知识后行还在不在 |
| [`03-接口清单.md`](03-接口清单.md) | 48 个端点怎么调；字段、错误码、示例；契约编号与代码编号的对应；前端函数映射 |
| [`04-实现与运维说明.md`](04-实现与运维说明.md) | 代码在哪；六个关键实现决策；配置项与调错的后果；按症状排障；测试覆盖与已知限制 |