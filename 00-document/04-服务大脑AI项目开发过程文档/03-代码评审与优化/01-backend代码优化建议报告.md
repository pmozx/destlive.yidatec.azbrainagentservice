# 01 · 服务大脑后端（backend）代码优化与简化建议报告

> 分析范围：`backend/app` 全部项目代码（排除 `.venv`）
> 分析维度：代码结构、模块划分、重复逻辑、性能、可维护性、正确性
> 分析日期：2026-11-02 · **当前版本：v2.0（瘦身重写）**

**v2.0 说明**：v1.0–v1.7 的逐条修复记录（含每次的验证表、DDL 清单、失败排查过程）已压缩为 §一 的一行式结论，篇幅让给 **§二 未完成项**。历史细节查 `git log` 与代码内注释；有长期价值的教训收进 §四「踩坑备忘」。

---

## 〇、一页看板

| 分级 | 已关闭 | 未完成 |
|---|---|---|
| P0 正确性/数据安全 | P0-1/2/3/4/5/6/7/10（P0-9 已失效） | **P0-8** |
| P1 资源/性能/稳定性 | P1-1/6/7/8/9/10（P1-2/3/4/5 已失效） | — |
| P2 一致性/可维护性 | P2-1 主体、P2-3 异常透传、P2-4 全部失效、P2-6 三项、**P2-7 结构性列默认值** | **P2-1 余 1、P2-2 余 2、P2-3 余 1、P2-5、P2-6 余 3、P2-7 余项** |
| P3 清理/测试补强 | P3-4/5/6 | **测试缺口** |
| 运维（非代码） | — | **P0-6 口令轮换** |

**一句话结论**：所有"会在生产咬人"的问题（脏数据提交、幂等锁死、并发覆盖、软删黑洞、错误码语义、契约字段无落点）已全部关闭，`pytest` **132 项**（按被测对象分 4 个包，见 `backend/tests/README.md`）/ `ruff` 全通过 / dev 库结构性列默认值已与模型对齐。

**v2.2 已完成 3 项**：**A-2**（63 个结构性列补 `server_default`，dev 库已对齐）、**A-1**（`base_model.py` 删 `index=True` 治根 + 防回退测试）、**脚本收敛**（`upgrade_schema` / `schema_upgrade` 已移除，其独有的两条 JSONB 表达式索引迁入模型）。**A-1 的库侧 DROP INDEX 仍需在 dev 库执行一次**（16 条，SQL 见 A-1 节）。

剩余项按"能不能现在做"分成四组，§二 是全文重点：

| 组 | 判据 | 条目 | 数量 |
|---|---|---|---|
| **A 可直接动手** | 无外部依赖、无需产品决策 | A-1（库侧收尾）、A-3 ~ A-6 | 5 |
| **B 需先决策** | 取决于产品/架构选择 | B-1 ~ B-3 | 3 |
| **C 等立项** | 依赖 #04 / #07 / #14 落地 | C-1 ~ C-4 | 4 |
| **D 有意不做** | 已评估，收益低于改动面 | D-1 ~ D-7 | 7 |

---

## 一、已关闭（速览，不展开）

### 1.1 代码修正

| 条目 | 结论 | 版本 |
|---|---|---|
| 删除残留（10 项断裂 import / lifespan 五段失效初始化 / 前端 re-export） | 应用恢复可启动，`/healthz` 200 | v1.2 |
| P0-1 契约路由吞异常导致错误提交 | `db_getter` 显式事务 + 提交前读 `contract_failed` 标记回滚 | v1.2 |
| P0-2 幂等键永久锁死 | `try/except BaseException/else` 三分支 + `asyncio.shield(release)` | v1.2 |
| P0-3 并发下 revision 可被覆盖 | `advance()` 条件更新（CAS）+ `create` 捕获 `IntegrityError` → 409 | v1.5 |
| P0-4 软删除基础设施失效 | **整体移除**（16 表 32 列 + 40 索引），删除语义改为纯物理删 | v1.7 |
| P0-5 业务错误默认 500 | 默认 400、`get_or_404` 固定 404、prod 下 5xx 话术固定、CRUD 不再吞异常 | v1.5 |
| P0-6 明文口令硬编码 | 默认空串 + 启动期校验 + `hide_input_in_errors`（**口令轮换待运维**） | v1.5 |
| P0-7 旧迁移基线在空库不可用 | 迁移工具已移除，缺陷随之关闭 | v1.5 |
| P0-10 契约必填字段无落点 | 6 列入库 + 归属校验升级为 `tenant_id+resource_type+resource_id` 三元组 | v1.6 |
| P1-1 客户端每请求重建 | 进程级单例 + `aclose()` + 关停收尾 | v1.5 |
| P1-6 查询条件导致 500 / 缺索引 | 列不存在则跳过 + 4 台账索引 + 2 条**表达式 B-tree** | v1.5/v1.6 |
| P1-7 向量化串行无重试 | `gather + Semaphore` 并发分批 + 指数退避 | v1.5 |
| P1-8 正文解析 4 次 | `core/request_body.py` 解析一次并缓存；multipart 只记元信息 | v1.5 |
| P1-9 断言不存在的错误码 | 补 `UNSUPPORTED_STREAM`，抽 `contract/testing.py` 共用 | v1.5 |
| P2-1 死代码 | 约 700 行零引用代码删除 | v1.5 |
| P2-3 CRUD 吞 `IntegrityError` | 11 处改「记日志 + 原样上抛」，交全局 handler 映射 409 | v1.5 |
| P2-6 配置重复 | 驱动映射去重、`AUTOFETCH` 删除、`403_SERVICE_UNAVAILABLE` → 503 | v1.5 |
| P3-4/5/6 | loguru 口径、向量层初始化加锁、端口与表数文档漂移 | v1.5 |

### 1.2 三个需要记住"为什么这么做"的决策

**P0-4 为什么删软删而不是补 `deleted_id`**：补列让软删生效会立刻制造三个新问题——软删的 chunk 向量**仍会被 pgvector 召回**（直接违反 #06"删除后不可召回"）；唯一键会开始挡"删了再建"（重新发布报 409）；"记录是否存在"出现两个互相矛盾的答案。而换来的"恢复窗口"是零需求的假想收益（从无回收站。代价已写入需求 `02-项目需求.md` DR-10：**不承诺删除恢复**）。

**契约字段为什么全部可空**：`NOT NULL` 在非空库上加不上去；`NOT NULL + DEFAULT ''` 会造出一批"看起来有值"的假归属数据（空 `tenant_id` 让越权校验形同虚设）。口径是「列可空、注释写明存量 NULL、代码写入必填、校验时 NULL 降级」。

**`security_check` 为什么不是 GIN 索引**：代码里的过滤是 `->> 'field' = 'value'`（`astext` 等值），`->>` 已把值取成 text，`GIN(jsonb_ops)` 只服务 `@>`/`?` 这类包含查询，对"提取后等值"不会被选中——按原报告建 GIN 会得到一个**永不被选中的死索引**。实际建的是表达式 B-tree。

### 1.3 能力现状（删除 integration 模块的代价，需所有读者知道）

删掉的不只是"重复实现"，而是**唯一的实现**。当前服务大脑的实际能力边界：

| 能力 | 现状 |
|---|---|
| 知识写入（#01/#02/#03/#05/#06） | ✅ 可用：解析 → 分片 → 向量化 → 落 `sbp_ai_kb_chunk` |
| **知识检索（#07）** | ❌ **无任何读路径**——原 `integration_kb/retrieval.py` 随模块删除，契约 #07 是 skeleton。这就是 **C-1（P0-8）** 的由来 |
| **问答/SSE（#14）** | ❌ 无：编排、重排、流式全部缺失。引用后处理已迁入 `app/core/chat/cite.py` 并保留测试，重建时可直接复用 |
| Prompt | ❌ 项目里没有任何 prompt 实现（原 `integration_chat/prompt.py` 已删），见 **B-3** |
| S3 对象存储 | ❌ 无消费方：`S3_*` 配置保留但已标注"当前无消费方"；契约 #01 走 `file_url` 直拉 |
| 文档解析 | ✅ `core/rag/pipeline.py` + MinerU（13 种二进制格式） |
| 台账/审计/权限 | ✅ system 域完整 |

**结论：系统当前是"写得进去、检索不出来"的状态。** 这对内不是 bug（#07 未立项），但对外不能宣称"写入即可检索"——见 C-1。

**已失效条目**：P0-9、P1-2、P1-3、P1-4、P1-5、P2-4（及 P2-2 的 S3/状态映射部分）随 `integration_*` 模块删除而消失，无需处理。逐条对照见 §三。

---

## 二、未完成（重点）

### A 组：可直接动手，无需决策

#### A-1 16 个冗余主键索引（P2-7 / 规范 D12 存量）—— 性价比最高的一条

**证据**（dev 库实测，2026-11-02）：

```sql
-- 16 条索引的定义全部形如：
CREATE INDEX ix_sbp_ai_kb_chunk_id ON public.sbp_ai_kb_chunk USING btree (id)
```

与主键索引 `sbp_ai_kb_chunk_pkey` **逐字相同**（同列、同方法、非部分索引、无额外排序）。涉及 16 张表：kb 7 张（chat_message/chat_session/chunk/dup_group/parse_job/publication/revision）、system 2 张（api_log/prompt_template）、voice 7 张。

**根因（v2.1 更正）**：不是"旧基座遗留"，而是 `app/core/base_model.py` 的 `id` 列上写了 `index=True`：

```python
id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
#                                                                          ^^^^^^^^^^ 冗余
```

主键本身已有唯一索引（`<table>_pkey`），`index=True` 又生成一条 `ix_<table>_id`。**一处参数影响 16 张表**——继承 `ModelMixin` 的正好 16 个；另外 5 张自己写 `id` 的表没加 `index=True`，所以没有冗余索引，16 这个数字正好对上。

**上一版判断错在哪**：`grep 'Index("ix_.*_id")'` 在模型里一条都没命中，于是误判成"模型层没有声明"。但 `index=True` 生成的索引**不是显式 `Index()` 对象**，从源码文本里搜不到，只有导出 `Table` 看 `table.indexes` 才会发现。

**后果（决定了改法）**：只删库里的索引、不改模型 → 下次任何人**按模型建表**（重建 dev 库、搭新环境）都会把 16 条原样建回来，因为索引是模型的属性。**必须同时改模型**。

> v2.1 时这里写的是"跑一次 `upgrade_schema` 就把 16 条全建回来"。该脚本已于 v2.2 移除，结论不变，只是触发路径变成"任何按模型建表的动作"。

**当前状态**：

| 侧 | 状态 |
|---|---|
| 模型 | ✅ 已删 `index=True`，并加 `tests/core/test_base_model.py::test_no_redundant_primary_key_index` 防回退 |
| 库 | ⚠️ dev 库 16 条仍在，待执行 DROP（见下方 DO 块） |

**原报告写"22 张表"是把所有以 `_id` 结尾的索引都算进去了**（库内共 22 条），其中 6 条是正常索引——`revision.version_id`、`vector_embedding.collection_id`、台账的 `request_id`/`biz_id` 与一条 `uq_..._request_id` 唯一约束。真正与主键完全重复的只有 **16 条**。

**代价**：每条冗余索引让每次 INSERT/UPDATE 多维护一棵 B 树；`api_log` 单表已 **807 行**且是最高频写入表。优化器永远不会选中它们（同列同法时优先用主键索引），纯写放大。

**库侧清理**：项目没有自动对齐工具（v2.2 起连脚本也没有了），这批只能手动清一次。不要裸写 16 条 `DROP INDEX`——"名字像冗余"和"真的冗余"必须分开判断，否则会误删 `ix_..._version_id` 这类名字同样以 `_id` 结尾的正常索引：

```sql
DO $$
DECLARE r record; n int := 0;
BEGIN
  FOR r IN
    SELECT i.indexname
    FROM pg_indexes i
    WHERE i.schemaname = 'public'
      AND i.tablename LIKE 'sbp_ai_%'
      AND i.indexname = 'ix_' || i.tablename || '_id'
      -- 两个条件同时成立才算冗余：定义恰为 btree(id)、且该表确有主键
      AND pg_get_indexdef(to_regclass(i.indexname)) LIKE '% USING btree (id)'
      AND EXISTS (
        SELECT 1 FROM pg_index pi
        WHERE pi.indrelid = to_regclass(i.tablename) AND pi.indisprimary
      )
  LOOP
    EXECUTE format('DROP INDEX IF EXISTS %I', r.indexname);
    RAISE NOTICE 'dropped %', r.indexname;
    n := n + 1;
  END LOOP;
  RAISE NOTICE '共删除 % 条冗余索引', n;
END $$;
```

**验收**：删完 `ix_<table>_id` 归零 → 导出 `KbChunkModel.__table__.indexes` 确认模型侧只剩复合索引（证明不会再被建回来）→ `pytest` 全绿。

---

#### A-2 `server_default` 全表缺失（P2-7 核心，已被真实踩过）✅ 已完成

> **v2.2 状态**：模型侧 63 个结构性列已补 `server_default`，dev 库已对齐。**v2.2 起 `upgrade_schema` 脚本已移除**——当时那 63 条 `ALTER` 是它批量生成的，今后同类变更需手写 SQL（模型里的 `server_default` 只在**新建库**时生效，存量库不会自己补）。以下是当时的判断与改法，保留供复盘。

**证据**：`grep -c server_default app/modules app/core` = **0**。所有默认值都只在 Python 侧（`default=`），DDL 里一个都没有。

**这不是理论风险**：v1.7 亲眼踩过——模型删了 `is_deleted`，而库里该列是 `NOT NULL` 且**无 server default**（`default=False` 只是 ORM 侧），插入直接违反非空约束，**15 个测试同时失败**，报的却是 `STALE_REVISION` / 409 / `NoResultFound`，与真因相距极远。任何裸 SQL、ETL、别的服务写这几张表，都会同样炸。

**建议改法**（分两步，先查后改）：

```sql
-- 1. 先拿候选清单（只读）
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema='public' AND table_name LIKE 'sbp_ai_%'
  AND is_nullable='NO' AND column_default IS NULL AND column_name <> 'id';
```

```python
# 2. 只给"结构性列"补默认值，业务语义列一律不补
status        → server_default=text("0")
created_time  → server_default=text("now()")
updated_time  → server_default=text("now()")
searchable    → server_default=text("false")
# ❌ tenant_id / resource_id / version_id / chunk_id 不补——
#    补 '' 就是造假的归属数据，宁可插入失败
```

**实际落地（v2.1）**：

| 步骤 | 结果 |
|---|---|
| `base_model.py` | `created_time` / `updated_time` 加 `server_default=text("now()")`，覆盖 16 张表 |
| 17 处 `status` | 各自补 `server_default`，值与该行 `default=` 严格一致 |
| 计数器 | `current_revision` / `message_count` / `progress` / `version` / `score` / `priority` / `chunk_count` 补 `text("0")`（或原值） |
| `revision.searchable` | 补 `text("false")` |
| `upgrade_schema`（已移除） | 当时新增了 `aligned_defaults` 动作生成这批 ALTER（模型有 `server_default` 且库里为 `NULL` 时 `SET DEFAULT`，库里已有别的值不覆盖）。**脚本 v2.2 已删**，今后同类变更手写 `ALTER ... SET DEFAULT` 并随 PR 交付 |
| dev 库 | 63 条 ALTER 全部成功、0 失败（已实测结构性列默认值缺失数 = 0） |

**哪些列有意不补**（重要，避免后来人"补全"）：剩下的 42 个非空列是 `uuid`（Python 函数默认值，DDL 无法表达）、JSONB 业务数据、业务字符串默认（`title='新会话'` 这类）。给它们补 `DEFAULT ''` 就是造"看起来有值"的假数据——与规范 **D18** 同一口径。

**验收**（已实测通过）：在临时 schema 建表后，裸 SQL 只写业务必填列——`INSERT INTO ... (publication_id, resource_type, uuid) VALUES (...)`——`status` 自动为 `'active'`、`current_revision` 为 `0`、`created_time` 有值，不再报非空错误。

---

#### A-3 P2-7 其余存量（数据库设计规范落地）

按"改动面 / 收益"排序，只有前两条值得现在做：

| 问题 | 现状证据 | 建议 |
|---|---|---|
| `naming_convention` 未设 | `grep naming_convention` = 0 命中 | 在 `Base.metadata` 挂 convention。**只对新库生效**（存量表名已固定，不会被重命名），价值是以后加的约束命名统一，消除"`uq_` 前缀误用于 `Index(unique=True)` 导致 `ON CONFLICT ON CONSTRAINT` 找不到约束"的坑 |
| `_at` / `_time` 混用，`BaseQueryParam` 只认 `_time` | 非 `_time` 的时间列共 6 处：`last_active_at`、`last_synced_at`、`expires_at`（auth_cache / idempotency / nonce / request_log 各一）；排序白名单只收 `_time` | 二选一：业务字段统一改名（跨端改动大）**或**让 `BaseQueryParam` 同时接受 `_at`（低风险，推荐）。后者是一行正则的事 |
| `status` 一半 int 一半 varchar、无 CHECK | kb 域 `status` 为 varchar，system/voice 部分为 int | 加 CHECK 前必须先确认代码不会写非法值，否则上线即报错。**暂缓** |
| 外键口径三种并存（物理 / 裸 String / 裸 int） | kb 删除时 revision/chunk 孤儿无清理机制 | 与 **C-3** 一起做（收敛分片存储时顺带定外键策略） |
| `Vector(settings.VECTOR_DIM)` 与 DDL 硬编码 1024 漂移 | `embedding.py:114` 有运行时校验兜底 | 用一条测试断言「模型里 `embedding` 列的维度 == `settings.VECTOR_DIM`」（原建议"在 `upgrade_schema` 里加核对"随脚本移除改为测试兜底） |

---

#### A-4 `core/base_crud.py` 689 行拆分

**现状**：689 行（v1.7 删软删逻辑后从 738 降下来），单文件承担：条件构造（`_build_conditions`）、排序白名单（`_parse_order`）、分页、preload、create/update 的审计字段重查。

**建议拆法**（按"会不会一起改"聚合，不按行数切）：

```
core/crud/
  base.py        # CRUDBase 主类，只剩方法编排
  conditions.py  # _build_conditions + 字段存在性判定
  ordering.py    # _parse_order + 可排序字段校验
  reload.py      # _reload_with_audit()：create/update 尾部两段完全相同的重查块
```

**前置条件（重要）**：`_parse_order` / `preload` 当前**零测试覆盖**（见 C-4）。先在 689 行的版本上补测试再拆，否则拆完无法证明行为未变。**顺序不能反。**

---

#### A-5 P2-2 剩余两处重复

| 重复 | 位置 | 建议 |
|---|---|---|
| 幂等薄壳 | `contract/idempotency.py:48 Reservation` vs `system/idempotency/service.py:29 IdempotencyOutcome` | 字段高度重合（`scope`/`biz_id`/`state`/`result`），语义都是"一次预约的结果"。合并为 `Reservation = IdempotencyOutcome` 别名并保留 docstring，或让前者继承后者 |
| 排序白名单 ×2 | `base_crud._parse_order:604` vs `vector_store._SORTABLE_FIELDS:55` | 统一走 `PaginationQueryParam` 校验，向量层只声明自己的允许字段集合 |

---

#### A-6 `SystemApiLogCreateSchema` 是"假事实源"（P2-1 余项）

**证据**：`app/modules/system/api_log/schema.py:6` 只有 12 个字段，而模型 `SystemApiLogModel` 有 20+ 列（`request_id`/`client_id`/`security_check` 等全缺）。它的唯一作用是填 `CRUDBase[Model, CreateSchema, CreateSchema]` 的泛型参数——实际写入走 `SystemApiLogService`，不走这个 DTO。

**风险**：后人照这个 schema 写审计会漏列，且"字段与 Model 保持一致"的 docstring 是错的。

**改法**：不建议"补齐字段"（那只是造出第二份事实源，还会让人以为它真的参与写入）。**推荐改口径**——把 docstring 从"字段与 Model 保持一致"改成事实：

```python
class SystemApiLogCreateSchema(BaseModel):
    """仅用于满足 CRUDBase 的泛型约束（CreateSchemaType 绑定 BaseModel），
    不参与审计写库——真实写入走 SystemApiLogService，字段清单以 SystemApiLogModel 为准。
    新增审计列时不必同步本类。"""
```

理由：`CRUDBase` 的 `CreateSchemaType` 有 `bound=BaseModel`（`base_crud.py:21`），传 `dict` 会破坏类型约束；而这个类一旦被补成"和 Model 一样"，就会变成需要长期维护的副本。

---

### B 组：需先决策

#### B-1 `user_id` 是否落独立列

**现状**：契约 `EntryWriteData` 携带 `user_id`，但库里只存在 `revision.metadata`（jsonb）与契约台账中，没有独立列。

**决策问题**：会不会有"按用户查自己发布过什么"的需求？

- 若 **#07 检索需要按用户过滤**（多用户共享库时极可能）→ 必须落列 + 索引，否则只能扫 JSONB（`->>` 等值在 jsonb 上可建表达式索引，但不如原生列直观）；
- 若只做审计追溯 → 现状足够（`api_log.username` + 台账可反查），**不建议加列**。

成本：加列 + 索引 + 写入链路各一处，沿用 v1.6 的可空列口径即可。**等你一句话。**

#### B-2 checkpointer：现在下线，还是留给 #14（P1-3 衍生）

**现状**：`core/rag/checkpointer.py` 零调用方（lifespan 的 init/close 已于 v1.2 删除）；库里 `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` 各 0 行、`checkpoint_migrations` 10 行（版本 0–9）常驻。

**两条路**：

| 选择 | 收益 | 代价 |
|---|---|---|
| 现在删文件 + DROP 4 张表 | 干净，少 4 张空表和技术债 | #14 重建断点续跑时要**重跑 10 次迁移** |
| 保留（现状） | #14 直接复用 | 库里躺着 4 张没人用的表 |

**建议**：保留文件、**暂不 DROP 表**（空表不影响任何事），但在 ADR 里写清"当前无断点续跑能力、`checkpointer.py` 为 #14 预留"。这条本质是 ADR 动作，不是代码动作。

#### B-3 `prompt_template` 空表：接表还是删表（P2-1 余项）

**现状**：`sbp_ai_system_prompt_template` **0 行**，代码**零引用**。原硬编码 prompt 的 `integration_chat/prompt.py` 已删，项目里现在**没有任何 prompt 实现**。

**二选一（#14 之前不用动）**：
- **接表**：带来进程内缓存 + 版本号 + 热更新的维护成本，但运营可改 prompt；
- **删表**：prompt 随代码版本管理（走 git review），ADR 记录该决策。

对当前状态（无 #14）而言，删表更符合"少一个假能力"的原则；但若产品要求运营侧可调 prompt，就必须接表。**需产品确认。**

---

### C 组：等对应链路立项

#### C-1 P0-8：`searchable` 的承诺仍然不成立

**这是唯一还开着的 P0。** `kb/publication/service.py:321` 写入 revision 时置 `searchable = accepted_chunk_count > 0`（有切片即 true），但全仓库**没有任何读路径**——原检索实现随 integration 模块删除，#07 仍是 skeleton。即"写入即可检索"这句承诺，代码层面无法兑现。

注意这不是"字段没写"的问题，而是**字段表达了系统还不具备的能力**：`searchable=True` 会出现在 #02/#06 的响应与 #04 的回查里，对 GSI 而言等于宣告"这条知识现在可以召回"。

**两种处理**：
- #07 上线前不置 `true`（固定 false + 注释"检索链路未就绪"）——诚实，但 #07 上线时要把存量 revision 全量回填一次；
- 保持现状，但**在对外文档里删掉"写入即可检索"的表述**，避免对 GSI 做出无法验证的承诺。

**判断依据**：回填成本很低（只涉及 `sbp_ai_kb_revision` 一个布尔列，`UPDATE ... SET searchable = (chunk 数 > 0)` 即可），所以不必为"省一次回填"而保留错误语义——

- #07 在近期有明确排期 → 选第二条（改对外文档 + 契约写明上线时间），省一次回填；
- **排期不确定 → 选第一条（固定 false）**，不要让系统长期对外宣告一个不存在的能力。

#### C-2 契约 #04（按版本/分片精读）仍是 skeleton

`contract/router.py:182` 仍是 `_skeleton(...)` 返回 501。字段侧已就绪（v1.6 落列：`revision.version_id`/`knowledge_no`/`metadata`、`chunk.chunk_id`、`ix_..._version_id`），**差的是查询实现**。属该接口立项范围。

#### C-3 两套分片存储必须二选一

| 表 | 角色 | 消费方 |
|---|---|---|
| `sbp_ai_kb_chunk` | 契约 #03 **唯一写入落点**，自带 vector 列 | publication/service 写入 |
| `sbp_ai_kb_vector_embedding` | 通用向量层（v1.4 从 `langchain_pg_*` 改造而来） | **零消费方** |

#07 落地时必须选一条：让通用向量层接管检索（`kb_chunk` 只存业务字段），或把 `search_documents` 改成直接查 `kb_chunk`。

**红线：不要两套并存写同一份数据。** 现在两套都在 `sbp_ai_` 命名空间内、谁主谁备一目了然，这是 v1.4 改造的唯一成果——不要退回到"库里冒出一张没人知道归谁的表"的状态。

#### C-4 测试缺口

| 缺口 | 现状 |
|---|---|
| `base_crud` 分页 / 排序 / preload | 只有条件构造与异常语义被覆盖（`test_base_crud_conditions.py` 5 项）|
| 业务 Service 层 | 只有 CAS 一项被覆盖（`test_publication_cas.py` 3 项），去重/冲突/孤儿清理语义无测试 |
| `/api/v1` 分页排序 | 无端到端验证 |
| DB 用例静默跳过 | 库不可达时自动 skip → **CI 不配库就"永远绿"**。建议加环境变量开关（如 `REQUIRE_DB=1` 时 skip 视为失败），否则这块绿灯没有意义 |

当前基线：15 个 `test_*.py`、**135 passed**（v1.2 时 6 文件 80 项）。

---

### D 组：有意不做（留档，避免后人重复提）

| 条目 | 为什么不做 |
|---|---|
| D-1 `setting.py`（307 行、单类 9 大域 100+ 配置）按子模型拆分 | 改动面大、收益偏组织性。等配置项继续增长、或出现"改一处要读全文件"的实际痛点时再做 |
| D-2 `RET` 成功码 0/200/201 混用、错误码 1/-1/4003~4506 三套段位 | 需同步前端契约，属跨端改动，单品后端改不动 |
| D-3 `core/router_class.py:148` 延迟 import `system/api_log/model`（core 反向依赖业务层） | 已有注释说明。彻底解耦要把"审计 DTO → 具体表"的映射下放到 system 域，改动面大于收益 |
| D-4 MySQL 兼容分支只做一半（`api_log` 有 LONGTEXT 分支，其余全用 JSONB/pgvector） | 文档已明确"仅支持 PostgreSQL 16 + pgvector"，分支属历史残留，不影响运行 |
| D-5 空壳 CRUD ×7（15 行/个，仅绑定 model） | 都被 service 引用，是 #07/#14 落地前的占位；`kb/*` 的 `BaseModel, BaseModel` 泛型参数当前无更好写法（PEP 696 类型参数默认值需 Python 3.13）。等对应 service 成型时连同 schema 一起补 |
| D-6 `core/loops.py` 改名 `event_loops.py` | 纯改名，需同步 `main.py` 与文档引用，收益极低 |
| D-7 `app/__init__.py` lifespan（91 行 / 5 段 try）抽 `(name, factory)` 列表 | v1.2 修复后每段失败话术都不同（"审计清理" vs "任务改判" vs "MinerU 接管"），强行抽象会丢掉这些语义。**现状可读性优于抽象** |

---

## 三、已失效 / 作废条目（防止重复提出）

| 原条目 | 现状 |
|---|---|
| P0-9 `resume_from=WRITE` 重跑 embedding | 失效：`document_pipeline.py` 已删 |
| P1-2 SSE 占满连接池 | 失效：问答链路已删。规范条目 F3/L15 保留，作为 #14 强制要求 |
| P1-3 双状态源 + checkpoint 冗余 | 失效：随流水线删除消失；checkpointer 空转转为 **B-2** |
| P1-4 上传/下载全量入内存 | 失效：`contract/parse.py:124` 的 `fetch_file` 本就是流式 + 超限中断，重建时以它为范本 |
| P1-5 投递早于提交 | 失效：契约 #01 "先提交后 202" 口径正确 |
| P1-10 两条知识链路冻结 | 已解决：删除即最彻底的冻结，kb 域为唯一链路 |
| P2-1 删 `core/rag/pipeline.py` | **作废并反转**：它现在是唯一流水线实现（`contract/parse.py` 依赖） |
| P2-2 S3 对象键 / 状态映射 8 份重复 | 失效：随 integration 删除 |
| P2-4 LangGraph 五个问题 | 失效：图编排已删。规范 4.3/4.4 节保留，作为 #07/#14 强制要求 |
| P2-5 `verified_signature` 129 行拆四步 | **已销项**：v1.5 把正文解析迁到 `core/request_body.py` 后，该函数降到 37 行，职责已清晰 |
| P2-5 `app/__init__.py` lifespan 122 行 8 段 | 已降到 91 行 / 5 段，且判定为**有意不做**（D-7） |
| 存量 `sbp_ai_integration_*` 表清理 | 无需处理：dev 库实测 0 张，清理脚本从未落地也不再需要 |
| `langchain_pg_*` / `vector_demo` | 已处置：前者改造为 `sbp_ai_kb_vector_*` 后 DROP，后者 DROP |
| `tests/core/test_chat_cite.py` 断裂 | 已修：改指 `app.core.chat.cite`，4 项用例保留 |
| `tests/debug_body.json` 疑似残留 | **不是残留**：验签用例的 29KB 固定字节样本，用途正当（2026-11-03 已规整：该样本迁至 `00-document/06-服务大脑AI项目测试用例与结果文档/test-data/requests/`，测试代码包内不再保留任何 JSON） |

---

## 四、踩坑备忘（只有真踩过才知道的）

| # | 坑 | 教训 |
|---|---|---|
| 1 | 表达式索引要**双层括号**：`ON t ((col ->> 'k'))`，单层报 `syntax error at or near "->>"` | PG 把括号内当"列名/表达式列表"解析，单层会被当成函数调用参数边界。现已写在 `SystemApiLogModel.__table_args__` 里，并在临时 schema 实测建出 |
| 2 | `GIN(jsonb_ops)` 对 `->> = 'v'` **无效** | `->>` 已取值成 text，GIN 只服务 `@>`/`?`。等值过滤要用表达式 B-tree。原报告建议是错的 |
| 3 | 新版 FastAPI 把 `include_router` 包成惰性 `_IncludedRouter`，`app.routes` 顶层只有 7 项 | 统计路由数**必须走 `app.openapi()["paths"]`**，按 `app.routes` 数会得出"0 条业务路由"的错误结论 |
| 4 | 模型与库结构漂移**只在真正写数据时炸** | 改完模型没跑 DDL，15 个测试立刻失败，报的是 `STALE_REVISION`/`NoResultFound`——与真因（`is_deleted NOT NULL` 无 server default）相距极远。**改模型必同步交付 `ALTER` 并确认已在目标库执行** |
| 5 | `starlette.status` 里没有 `HTTP_403_SERVICE_UNAVAILABLE` | 常量写错的分支只有数据库连不上时才执行，平时测不出来；真出事时 `AttributeError` 被兜成 500，「连接失败」看起来和「代码崩了」一模一样。**每个 `status.HTTP_xxx` 都要真的跑一次对应分支** |
| 6 | PG `DROP COLUMN` 会**连带删除依赖索引** | 删列后必须重查索引集合，否则下一轮会误判成"模型漏声明"而把索引重新建出来 |
| 7 | `ALTER TABLE` 拿 `ACCESS EXCLUSIVE` 锁 | 所以结构变更**绝不进启动路径**。`create_missing_tables` 只在表不存在时动手，正常启动不会拿这把锁；把 `ALTER` 塞进 lifespan 等于让改表阻塞服务启动，失败原因还会被启动日志淹没 |
| 8 | `index=True` 生成的索引**在源码里搜不到** | 它不产生显式 `Index()` 对象，搜 `Index("ix_...")` 一条都命中不了。要判断「某索引是谁建的」只能导出 `Table` 看 `table.indexes`，或直接看 `pg_get_indexdef` |
| 9 | 同一张表声明两条同名索引时**不会报错** | `table.indexes` 里是两个不同对象，建表时会执行两次 `CREATE INDEX`，第二次才炸。v1.7 批量改索引在 3 张表各留了一处重复，`test_base_model_defaults.py` 现在会拦住 |
| 10 | 对齐脚本**独有的结构定义**，删脚本前必须先搬进模型 | `EXTRA_INDEXES` 里两条 JSONB 表达式索引只存在于脚本；删脚本而不迁移的话，dev 库当下没事、**一旦重建库就永久缺失**，且不会有任何报错。删任何"补差"工具前，先让它导出「模型里没有的东西」 |

---

## 五、建议执行顺序

| 顺序 | 内容 | 判据 | 预计改动面 |
|---|---|---|---|
| **第 1 步** ✅ 完成 | **A-1** 冗余索引 + **A-2** `server_default` | 纯 DDL + 模型属性，零业务语义 | 已完成：`base_model.py` 删 `index=True`、63 列补默认值、dev 库默认值已对齐；`upgrade_schema` 已移除。**仅剩 A-1 库侧一次 DROP INDEX**（16 条，dev 库仍在） |
| **第 2 步** | **A-6** schema 副本 + **A-5** 幂等薄壳/排序白名单 + **A-3 前两条**（`naming_convention`、`BaseQueryParam` 接受 `_at`） | 纯收敛，不动行为 | 4-5 个文件 |
| **第 3 步** | **C-4 的一半**：给 `_parse_order`/`preload`/分页补测试 | 拆 A-4 的前置 | 1 个测试文件 |
| **第 4 步** | **A-4** `base_crud` 拆分 | 有第 3 步兜底才敢动 | 1 拆 4 |
| **决策后** | **B-1** `user_id`、**B-3** prompt 表 | 你定方向 | 各自 3 处以内 |
| **立项后** | **C-1/C-2/C-3** | 跟 #04/#07/#14 走 | 随链路 |

每步的收尾口径一致：`pytest -q` 全绿（当前基线 132）、`ruff check app tests` 通过、启动冒烟 `/healthz` 200；**凡动了模型的，确认对应 `ALTER` 已在目标库执行**。

配套动作：`#07`/`#14` 重建前先读 `01-团队开发规范.md` 第 4 章；改表结构的 PR 必须带 `ALTER` 语句（规范 D17）。

---

## 六、变更记录

| 版本 | 内容 | 关键结果 |
|---|---|---|
| v1.0 | 初版全量分析 | 22 项问题，P0 11 / P1 10 / P2 7 / P3 6 |
| v1.1 | 复评（integration 模块被删） | 新增 〇-A；两项"双实现"风险解除，一项部分解除 |
| v1.2 | 第 0 批：删除残留清理 | 应用恢复可启动（healthz 200），测试 80 项 |
| v1.3 | 文档收口 + 存量库核验 | 确认 `sbp_ai_integration_*` 已 0 张；发现 `langchain_pg_*` 等遗留 |
| v1.4 | 遗留对象处置 | 向量层重写为 `sbp_ai_kb_vector_*`，DROP 3 类表，库表 122 → 120 |
| v1.5 | 第 1 批：P0/P1 收口 + 死代码 | 修 P0-3/5/6、P1-1/6/7/8/9；删约 700 行；测试 100 → 118 |
| v1.6 | 第 2 批：契约字段 + 索引 | 6 列 / 7 索引 / 1 唯一约束；新增 `upgrade_schema` 入口；测试 → 128 |
| v1.7 | 第 3 批：软删整体移除 | 16 表 32 列 + 40 索引删除；测试 → 135 |
| **v2.0** | **瘦身重写** | 已完成压缩为速览表；未完成按 A/B/C/D 四组展开；修正 3 处过期数据（冗余索引 22→16 张、`base_crud` 738→689 行、`verified_signature` 拆分项销项） |
| **v2.1** | **A-1 / A-2 落地** | `base_model.id` 删 `index=True`（根因，覆盖 16 表）+ 63 个结构性列补 `server_default`；`upgrade_schema` 新增「补齐默认值」（dev 库 63 条 ALTER 全部成功）；修掉 3 处重复索引声明；测试 135 → 141 |
| **v2.2** | **移除结构对齐脚本** | 删 `core/schema_upgrade.py` + `scripts/upgrade_schema.py` + 对应测试；其独有的 2 条 JSONB 表达式索引迁入 `SystemApiLogModel.__table_args__`（临时 schema 实测建出）；规范 D17 重写为「建表只在启动时补缺表，存量变更交 DDL」；测试 141 → 134 |
| **v2.3** | **测试重组** | 15 个按批次命名的文件 → `core/`（51）/ `contract/`（44）/ `modules/`（34）/ `stages/`（3）四包；拆掉 `test_v1_contract.py`（744 行混 6 类对象）、合并三处同对象多文件；`test_generate_custom_headers` 降级为调试脚本（其断言会误伤 test/uat/prod）；测试 134 → 132 |
