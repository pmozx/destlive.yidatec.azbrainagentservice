# 01-服务大脑AI项目文档

> 服务大脑 AI 服务（对既有业务系统 GSI 做 AI 赋能：知识平台 kb + 语音质检 voice）的**当前权威项目文档基线**。
> 文档集版本 v1.0 · 2026-10-29 · 编制人 孟星旭

## 文档清单

| 文档 | 内容 | 关键词 |
| --- | --- | --- |
| [01-项目背景与描述](computer://d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\00-document\01-服务大脑AI项目文档\01-项目背景与描述.md) | 项目由来、痛点、定位（对 GSI 做 AI 赋能）、目标、范围与边界、干系人、假设约束、成功判据 | 两核心模块、共库、ChatGPT 5.6 |
| [02-项目需求](computer://d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\00-document\01-服务大脑AI项目文档\02-项目需求.md) | 功能需求（FR-INT/FR-AUD/FR-KB/FR-RAG/FR-DUP/FR-QC/FR-RPT/FR-AGT/FR-OPS）、模型需求、数据需求、非功能需求、追踪矩阵、待确认事项 | 20 接口契约、审计与安全头、sbp_ai_ 命名 |
| [03-项目架构设计](computer://d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\00-document\01-服务大脑AI项目文档\03-项目架构设计.md) | 六层分层、容器拓扑、14 条 ADR、接入层（签名流程）、审计架构（安全头校验扩展）、编排/能力/模型/数据架构、部署运维 | LangGraph、权限下推、public schema 共库 |
| [04-项目模块说明与表结构设计](computer://d:\workspace\ws_work\fastapi_admin_xian\service_brain_agent\00-document\01-服务大脑AI项目文档\04-项目模块说明与表结构设计.md) | `sbp_ai_[模块名]_[业务名]` 命名强规范、共库治理（Alembic/Flyway 分治）、system/kb/voice/integration 四域 23 张表逐表设计、GSI 共享表关联、生命周期、设计约束 12 条 | 表结构、审计扩展列、pgvector |

## 阅读顺序

1. 先读 `01` 了解为什么做与边界 →
2. 再读 `02` 的 §三（职责划分）与 §十二（待确认事项）→
3. 开发实现按 `02` 需求 + `03` 架构 + `04` 表结构对照执行。

## 与既有文档的关系

| 目录 | 关系 |
| --- | --- |
| `../00-项目前期准备资料/` | 上游输入（解决方案、质检需求、知识样例），保持不变 |
| `../01-服务大脑项目文档/` | 原 v1.8 契约版项目文档，**已于 2026-10-29 清理删除**，由本目录取代 |
| `../02-服务大脑AI项目开发文档/` | 契约与 GSI 表结构的**事实来源**（v2.1 接口手册、93 表设计），并含开发计划（`01-1`/`01-2`/`01-3`：POC 先行，17 个自然周 / 63.5 人日 / M0–M8）；本目录在其上确立本项目侧口径 |

## 本期关键口径（速览）

- 契约基线：v2.1.0，**20 接口 / 18 路径**（#01–#20 顺序编号，含重复治理 #16–#20）；HMAC 四头 `X-AI-Client/Timestamp/Nonce/Signature`
- 数据归属：与 GSI **共用 `service_brain` 库、public schema**；AI 表一律 `sbp_ai_[模块名]_[业务名]`（模块码：`system`/`kb`/`voice`，`integration` 为过渡域）；Alembic 与 GSI Flyway 分治
- 权限：按签名身份实时只读查询 **7 张 GSI 共享表**计算授权，SQL 下推，禁止降级为全库检索
- 审计：20 个 `/v1/*` 接口全部纳入统一日志审计 `sbp_ai_system_api_log`，并**扩展 HTTP 安全头校验留痕**（`auth_mode`/`client_id`/`request_id`/`security_check`），被拒请求同样落审计
- 模型：对话 **ChatGPT 5.6**（OpenAI 兼容端点、配置化）；向量化 bge-m3（1024 维）与重排 bge-reranker-v2-m3 私有化部署；向量存储 pgvector（HNSW 余弦）
- 编排：LangChain 1.3 + LangGraph StateGraph（工程已集成）
- 开发计划：**POC 先行（接口复核暂缓）**，2026-11-02 ~ 2027-02-26，17 个自然周 / 78 个工作日 / 63.5 人日 / M0–M8（见 `../02-服务大脑AI项目开发文档/01-2-知识平台开发计划与说明.md`）；语音质检暂缓
