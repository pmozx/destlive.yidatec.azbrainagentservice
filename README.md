# 服务大脑（service_brain_agent）

基于 **FastAPI + Vue 3** 的前后端一体化应用工程：后端提供 `/v1` 契约接入面（解析任务与知识条目写入已实现，检索/会话为骨架）与 `/api/v1` 管理面（日志审计、接入调用追踪、GSI 业务只读查询），前端为与之同源部署的监控台，构建产物由 FastAPI 的 `/ui` 直接托管。

- 后端：FastAPI 工厂模式 + 异步 SQLAlchemy 2.0 + APScheduler + loguru，业务模块集中在 `app/modules`，GSI 业务只读访问集中在 `app/modules_gsi`，由路由总表显式注册，同时提供 Swagger / ReDoc 接口文档
- 前端：Vue 3 + Vite 7 + Element Plus，顶部横向导航 + 白色大圆角卡片布局，酒红紫主题，支持深色模式与主题色切换
- 工程化：uv 管理 Python 依赖、npm 管理前端依赖；pytest + ruff；SCSS（BEM + mixins）

> **能力边界（务必先读）**：知识库域（`kb`）与语音质检域（`voice`）当前**只有数据模型与表结构，没有 controller**，即无 HTTP 出口。对外能力 = `/v1` 契约面（**20 条中 5 条已实现业务逻辑，其余 15 条为骨架，返回 501**）+ `/api/v1` 管理面（日志审计、接入调用追踪）+ `/api/v1/gsi` 只读查询面（8 张 GSI 共享表、13 条 GET，无写入）。原 `integration` 过渡域（知识空间、分片 CRUD、对象存储、集成自检端点）已整体删除，删除决策与能力回退清单见 [`00-document/04-.../03-代码评审与优化/01-backend代码优化建议报告.md`](00-document/04-服务大脑AI项目开发过程文档/03-代码评审与优化/01-backend代码优化建议报告.md) 的 〇-A 章。

## 技术栈

| 层次 | 选型 |
| --- | --- |
| Web 框架 | FastAPI、uvicorn[standard] |
| 数据层 | SQLAlchemy 2.0（async）、asyncpg（PostgreSQL 主选）/ aiomysql / aiosqlite |
| 向量存储 | PostgreSQL + pgvector（`pgvector` Python 包 + HNSW 余弦索引）；业务向量落 `sbp_ai_kb_chunk.embedding`，通用向量层为 `sbp_ai_kb_vector_collection` / `sbp_ai_kb_vector_embedding` |
| 对象存储 | S3 协议兼容（`app/core/clients/s3.py`，boto3）：MinIO / 华为云 OBS / 阿里云 OSS 均可，靠 `S3_PROVIDER` 切换寻址风格 |
| 文档解析 | MinerU（`mineru-open-sdk`）：PDF / Office / 图片 → Markdown + 版面 JSON（页码溯源）；纯文本/HTML 走内置解析器。实现位于 `app/core/rag/`，当前仅契约 #01 解析任务使用 |
| 配置与日志 | pydantic-settings、python-dotenv、loguru |
| 调度 | APScheduler 3.x |
| 前端 | Vue 3.5、Vite 7、Vue Router 4、Pinia 3、Element Plus 2.x、SCSS(sass) |
| 质量 | pytest、pytest-asyncio、ruff |

## 目录结构

```text
service_brain_agent/                # 仓库根：前后端一体化工程
├── backend/                       # 后端工程（Python 依赖与运行目录的唯一入口）
│   ├── main.py                  # 启动入口（默认 0.0.0.0:8002，热重载）
│   ├── app/
│   │   ├── __init__.py          # create_app：lifespan、路由注册、前端托管
│   │   ├── api/v1/routers.py    # 路由总表：域前缀 → 该域 controller 清单（唯一路由事实来源）
│   │   ├── contract/            # /v1 契约接入面：签名/nonce/幂等/审计、#01–#20 端点、解析编排
│   │   ├── modules/             # 业务模块：域/功能两级，表名统一前缀 sbp_ai_[域缩写]_
│   │   │   ├── system/         # 系统管理：接口审计日志 + 接入调用追踪（仅此域有 controller）
│   │   │   │   ├── api_log/    # 日志审计（sbp_ai_system_api_log）
│   │   │   │   └── access_trace/ # 接入调用追踪：nonce / 幂等台账 / 请求台账概览
│   │   │   ├── kb/             # 知识库域：8 个功能目录，模型与 CRUD 已就绪，**暂无 controller**
│   │   │   │   └── (chunk / publication / revision / parse_job / parse_result / dup_group / chat_session / chat_message)
│   │   │   └── voice/          # 语音质检域：8 张表模型已就绪，**暂无 controller**（二期）
│   │   ├── modules_gsi/         # GSI 业务只读：模块/业务两级，独立 metadata，不参与启动建表
│   │   │   ├── sys/            # org / org_member / audience_team / audience_team_member / user
│   │   │   └── km/             # doc / doc_publication / doc_record
│   │   ├── core/                # 数据库、依赖、异常、日志、审计路由类、校验器
│   │   │   ├── rag/            # 解析/分片/向量存储/流水线/MinerU（契约 #01 与 #03 使用）
│   │   │   ├── chat/           # 引用构造、重排、流式工具（纯函数，解耦业务模型）
│   │   │   └── clients/        # LLM / Embedding / Rerank / MinerU 客户端
│   │   ├── config/              # 路径常量 + Settings（多环境）
│   │   ├── common/              # 常量、枚举、响应体、请求体
│   │   └── utils/               # banner、密码、通用工具
│   ├── env/                     # 多环境配置：.env / .env.dev / .env.test / .env.prod ...
│   ├── static/                  # 前端构建产物（/ui 托管）与后端静态资源
│   ├── tests/                   # pytest 用例
│   ├── logs/  data/             # 运行日志、SQLite 数据文件
│   ├── pyproject.toml / uv.lock # 依赖声明与锁文件（uv sync / uv run 均在 backend/ 下执行）
│   └── pytest.ini / .python-version / banner.txt
├── frontend/                    # Vue 3 前端工程
│   ├── src/
│   │   ├── api/                 # 接口封装（module_system / module_monitor）
│   │   ├── layouts/             # 顶栏、页签、设置抽屉、布局骨架
│   │   ├── views/               # dashboard、module_system、module_monitor、error
│   │   ├── components/          # SectionCard、StatCard、JsonViewer、TaskStatusTag
│   │   ├── assets/styles/       # SCSS：index / reset / variables / mixins
│   │   ├── router/ store/ config/ hooks/ utils/ enums/
│   │   └── main.js
│   ├── vite.config.js           # base=/ui/，构建输出到 ../backend/static
│   └── scripts/clean.mjs        # 构建前精确清理 backend/static
└── devops/                      # CI/CD 与部署（Dockerfile / compose / 脚本，详见 devops/README.md）
```

## 快速开始

### 后端

```bash
# 进入后端工程目录（Python 依赖与 main.py 均在此）
cd backend

# 安装依赖（含 dev 组）
uv sync

# 启动（backend/ 下已带 .venv 时也可直接运行）
python main.py
# 等价写法
uvicorn app:create_app --factory --host 0.0.0.0 --port 8002 --reload
```

| 入口 | 地址 |
| --- | --- |
| 前端页面 | http://127.0.0.1:8002/ui |
| Swagger | http://127.0.0.1:8002/docs |
| ReDoc | http://127.0.0.1:8002/redoc |
| OpenAPI JSON | http://127.0.0.1:8002/openapi.json |

### 前端

```bash
cd frontend
npm install
npm run dev     # 开发服务器 5173，访问 http://127.0.0.1:5173/ui/
npm run build   # 产物写入 ../backend/static，随后由 FastAPI /ui 托管
```

业务接口统一挂在 `/api/v1` 下（`app/api/v1/routers.py` 的 `API_PREFIX`），开发环境只需把该前缀经 Vite 代理转发到 `http://127.0.0.1:8002`；`/docs`、`/openapi.json`、`/healthz` 为应用级入口，仍在根路径，开发环境一并转发。前端 axios 默认 baseURL 为 `/api/v1`（`VITE_APP_BASE_API`），因此页面与接口始终同源，生产环境无需改配置。

## 配置说明

### 后端多环境

`backend/app/config/setting.py` 按 Spring Boot profiles 的方式选择配置：

1. 优先读取系统环境变量 `ENVIRONMENT`
2. 其次读取 `backend/env/.env` 中的 `ENVIRONMENT`
3. 都未设置时使用 `dev`，加载 `backend/env/.env.dev`

数据库类型由 `DATABASE_TYPE` 决定（`postgres` / `mysql` / `sqlite`），异步驱动与连接串自动拼接；连接池、会话、日志级别均可在 `backend/env/.env.{environment}` 中覆盖。

本工程默认使用 **PostgreSQL**，并通过 **pgvector** 承担向量存储：

```ini
DATABASE_TYPE=postgres
DATABASE_HOST=localhost          # 容器部署时改成 service-brain-postgres
DATABASE_PORT=39990              # 自建编排的宿主发布端口；容器内按容器名直连用 5432
DATABASE_USER=postgres
DATABASE_PASSWORD=Yidatec@2026!
DATABASE_NAME=service_brain
VECTOR_ENABLE=true
VECTOR_DIM=1024                  # 须与 embedding 模型输出一致（bge-m3=1024）
VECTOR_AUTO_CREATE=true          # 启动时只建 vector 扩展；分片表由 kb 域 bootstrap 建
```

向量落点：契约 #03 写入时由 `EmbeddingClient` 批量生成向量，直接落 `sbp_ai_kb_chunk.embedding`（`KbEntryService._build_chunk_rows`），维度不符整批拒绝、不入库。`core/rag/vector_store.py` 另有一套通用向量层（表 `sbp_ai_kb_vector_collection` / `sbp_ai_kb_vector_embedding`，SQLAlchemy Core 直接写 SQL，表名与索引名自持），当前**仅 `validate_embedding` 被调用**，`upsert_documents` / `search_documents` 等无真实消费方。解析链路（`contract/parse.py`）只做「解析 → 分片」（`core/rag/parse_and_chunk`），不负责向量化。

### 对象存储（解析页图）

`S3StorageClient` 曾随 `integration_kb` 域删除；现在的实现是 `app/core/clients/s3.py` 的 `S3Client`（boto3 + IO 线程池，进程级单例），消费方只有解析页图预览：PDF 页图渲染后上传到 `parse-previews/<job_id>/page-<n>.png`（再加 `S3_KEY_PREFIX`），`#02 result.pages[].image_url` 返回**预签名 GET URL**，不再落本地 `static/parse-previews`、也不再自建 `/previews/parse` 预览路由。

启用只需把 `S3_ENABLE=true` 并填好 endpoint / 凭据 / 桶；`S3_AUTO_CREATE=true` 时启动期幂等建桶（失败只告警）。关掉 `S3_ENABLE` 即退化为 `pages=[]`，解析本身不受影响。MinIO 编排（`devops/common-server/docker-compose-minio.yml`）可直接复用，更完整的口径见 [devops/README.md 6.6](./devops/README.md)。

### 知识库域现状（模型就绪，无 controller）

kb 域 8 张表（`sbp_ai_kb_*`）由 `app/modules/kb/bootstrap.py` 在启动时幂等建好，CRUD 与服务层也在各功能目录内，但**没有任何 controller**，因此不存在 `/api/v1/kb/*` 端点。知识能力的唯一入口是 `/v1` 契约面（见下节）。

原 `/api/v1/integration/space/*`（知识空间、文档上传/解析、产物预览）与 `/api/v1/integration/chunk/*`（分片 CRUD、相似度检索）**全部随模块删除**；`ensure_space_tables()`、`reset_interrupted_documents()`、`space/task.py` 任务登记表均不再存在。

进程重启后的中断恢复改为 `KbParseJobService.reset_unfinished()`（`KbParseJobCRUD.fail_unfinished()` 一条 UPDATE 批量改判 failed），不再有「解析中→待解析」的回退语义。

### GSI 业务只读访问（modules_gsi）

GSI 业务表以 `sbp_` 开头（无 `ai` 段），由 GSI 侧 Flyway 创建与维护，AI 侧只读：

- 代码：`app/modules_gsi/<模块名>/<业务名>/`，按 `model / crud / schema / service / controller` 五层组织，类名统一 **Sbp** 前缀（如 `SbpSysOrgModel` / `SbpSysOrgCRUD` / `SbpSysOrgService` / `SbpSysOrgRouter`）；
- 范围：`sys`（`org` / `org_member` / `audience_team` / `audience_team_member` / `user`）与 `km`（`doc` / `doc_publication` / `doc_record`），共 8 张表；
- 接口：`/api/v1/gsi/*` 共 13 条 GET 只读查询（列表 8 条 + 详情 5 条），登记在 `DOMAIN_CONTROLLERS` 的 `/gsi` 域；
- **不建表**：模型使用独立 `SbpBase.metadata`，`app/bootstrap.py` 不登记该包，启动期不会创建或修改任何 GSI 表；表结构以 `02-服务大脑GSI业务系统表结构.sql` 为准；
- **只读**：CRUD 仅 `get / get_list / count / exists / page`，无 create / update / delete；`sbp_sys_user` 只映射契约允许字段，不映射 `password_hash`、邮箱、手机号等敏感列。

### 契约接入面 `/v1`

由 `app/modules_api/contract/` 提供，`CONTRACT_V1_ENABLE=true` 时经 `register_api(app)` 挂载（独立于 `/api/v1` 管理面）。所有端点统一走签名 + nonce + 幂等 + 审计链路（`ContractApiRoute`），即使骨架端点也已具备这三层保护；签名与错误码口径见 `00-document/01-.../05-接口契约与数据基线/`。

| 端点 | 状态 | 说明 |
| --- | --- | --- |
| `POST /v1/parse/jobs` | ✅ 已实现 | #01 提交解析任务：`file_url` 同步拉取、10MB/类型校验、解析在线程池执行 |
| `GET /v1/parse/jobs/{job_id}` | ✅ 已实现 | #02 查询解析进度与完整结果 |
| `POST /v1/knowledge/entries` | ✅ 已实现 | #03 首次发布写入正式切片（含 embedding 落库） |
| `PUT /v1/knowledge/entries/{publication_id}` | ✅ 已实现 | #05 修订：版本切换 / 单片删除 |
| `DELETE /v1/knowledge/entries/{publication_id}` | ✅ 已实现 | #06 整单删除 |
| `POST /v1/knowledge/entries/{id}/query` | ⛔ 骨架 501 | #04 读取当前生效基线 |
| `POST /v1/knowledge/search` | ⛔ 骨架 501 | #07 关键词 / 自然语言检索 |
| `/v1/chat/sessions*`（#08–#13） | ⛔ 骨架 501 | 会话创建、分页、详情、改标题、删除、清空历史 |
| `POST /v1/chat/completions`、`/chat/requests/{id}/query` | ⛔ 骨架 501 | #14/#15 非流式问答、超时结果查询 |
| `/v1/knowledge/duplicates/*`（#16–#19） | ⛔ 骨架 501 | 查重、分组查询、分组详情、合并预览 |
| `POST /v1/knowledge/entries/{id}/deactivate` | ⛔ 骨架 501 | #20 停用已生效知识 |

骨架端点返回 `501` + `INTERNAL_ERROR`（`retryable=false`），并在错误体里原样带回 `request_id`——这是刻意设计的明确信号，不是路由缺失。业务实现随 Tx 逐个替换，契约形状不变。

### 模型服务（LLM / Embedding / Rerank）

客户端实现位于 `app/core/clients/`（`llm.py` / `embedding.py` / `rerank.py` / `mineru.py` / `s3.py`），均为独立客户端，不依赖业务模块。按需填写配置，缺省留空即视为“未接入”，调用时报 503 并提示该填哪个变量，不做静默降级：

```ini
# 大语言模型（OpenAI 兼容：DeepSeek 直连 / 自建 vLLM / Azure 网关）
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=

# 向量化：protocol=openai 走 /v1/embeddings，protocol=ollama 走 /api/embed
EMBEDDING_PROTOCOL=openai
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=bge-m3
EMBEDDING_BATCH_SIZE=16
EMBEDDING_CONCURRENCY=3       # 分批并发数：300 分片 × batch16 = 19 批，串行要等 19 次往返
EMBEDDING_MAX_RETRIES=2       # 单批失败重试次数（指数退避 0.5s/1s/2s…），耗尽才判整单失败

# 重排序：llama.cpp --rerank（Jina / Cohere 同构报文）
RERANK_BASE_URL=
RERANK_PATH=/v1/rerank
RERANK_MODEL=bge-reranker-v2-m3

# 文档解析线程池与分片：CPU/阻塞 IO 不占事件循环，分片按字符数而不是 token 计
PARSE_THREAD_POOL_SIZE=4
PARSE_CHUNK_SIZE=800
PARSE_CHUNK_OVERLAP=120

# MinerU 文档解析（当前暂定的内容提取主路径）：只写 env，密钥不入库、不进代码
MINERU_ENABLE=true               # false 时 PDF/Office/图片直接报 415（不再静默降级）
MINERU_API_TOKEN=                # 留空 = flash 模式（免 token，≤10MB 且 ≤20 页，仅 Markdown）
MINERU_BASE_URL=                 # 留空用 SDK 官方地址；私有化部署时覆盖
MINERU_FLASH_BASE_URL=
MINERU_MODEL=vlm                 # 精准解析模型：vlm / pipeline / html
MINERU_TIMEOUT=300               # 轮询总超时（秒），大文档成分钟级
MINERU_OCR=true                  # 扫描件必须开
MINERU_LANGUAGE=ch
```

解析器分工（`app/core/rag/parsers.py` + `app/core/rag/mineru.py`）：

| 文件类型 | 处理方式 |
| --- | --- |
| `.pdf` `.doc(x)` `.ppt(x)` `.xls(x)` `.png/.jpg/.jpeg/.bmp/.tif/.tiff/.webp` | MinerU（启动时由 `install_mineru_parser()` 按 `MINERU_ENABLE` 注册，`prepend` 优先） |
| `.txt` `.md` `.csv` `.json` `.yaml` `.log` | 内置纯文本解析器（UTF-8 BOM → UTF-8 → GB18030），不浪费一次上传 |
| `.html` `.htm` | 内置 HTML 解析器（剥离标签与 script/style） |

MinerU 返回的 `content_list` 会转成「文本 → 页码」映射，分片时用单调游标按文本前缀回填 `page_no`（一级匹配不上退到窗口匹配、仍不中沿用上一块页码）。
两件事要注意：

1. **flash 与 precise 差别很大**：未配 `MINERU_API_TOKEN` 走 flash，只回 Markdown —— 既没有 `content_list`（页码只能留空），也没有图片（落不了 `content_list.json` / `images/`）。要页码与完整产物必须配 token 走 precise；
2. MinerU 的提交+轮询是分钟级阻塞调用，一律在解析线程池里执行，不占事件循环。

解析产物正文不入对象存储（`full.md` / `content_list.json` 只作为契约 #01/#02 的返回载荷存在），落库的是 `sbp_ai_kb_parse_result` 与 `sbp_ai_kb_chunk`；**唯一进对象存储的是 PDF 页图**（`parse-previews/` 下，供 `#02 result.pages` 预签名预览）。

联调验收用 `app/scripts/contract/verify_m1.py`（服务已启动后执行，退出码非 0 即验收不达标）：

```bash
# 在 backend/ 下执行
.venv/Scripts/python.exe -m app.scripts.contract.verify_m1 --base-url http://127.0.0.1:8002
# 需要把验收记录写成 Markdown 时追加：--record <输出路径>
```

它用 Mock GSI 五头签名打通 20 条 `/v1` 路由，逐条断言签名/nonce/错误码，并直连核对审计与幂等台账。原 `GET /api/v1/integration/probe` 集成自检端点已随模块删除。

### 前端环境

| 文件 | 用途 |
| --- | --- |
| `.env.development` | 标题、`VITE_APP_BASE=/ui/`、接口前缀 `VITE_APP_BASE_API=/api/v1`、代理目标 `VITE_API_BASE_URL` |
| `.env.production` | 标题、base、接口前缀（页面与接口同源，无需跨域配置） |

## 前端功能

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/dashboard` | 运行总览 | Hero 检索、指标条、最近任务（示例数据）、快捷入口、后端服务信息 |
| `/system/log` | 日志审计 | 对接 `/api/v1/system/api-log`，分页查询、条件筛选、详情、批量删除、按天数清理 |
| `/system/trace` | 接入调用追踪 | 概览指标 + 调用明细（`api_log` 按 `request_id` 精确匹配）、防重放登记（`nonce`）、幂等台账、按 `request_id` 的调用链路时间线 |
| `/monitor/endpoint` | 接口自检 | 接口清单 + 逐个探测，JSON 展示响应与耗时 |

> 原 `/integration/space`（知识空间）、`/integration/probe`（集成自检）、`/integration/chunk`（文档分片）三个页面已随模块删除，前端 `views/module_integration` 与 `api/module_integration` 目录均不存在。

布局与交互：

- 固定顶栏（深紫 → 品红渐变）：品牌区、横向菜单、明暗切换、刷新、全屏、界面设置、用户下拉
- 菜单选中统一为白色胶囊；进入子路由时父级菜单通过 `is-current` 保持与一级菜单一致的选中样式
- 面包屑 + 页签胶囊（页签数量 > 1 时显示），页面切换带过渡动画
- 界面设置抽屉：深浅模式、主题色预设（酒红紫/品红/黛紫/拂晓蓝/极光绿）、页签与面包屑开关、恢复默认

## 样式体系（SCSS）

`src/assets/styles/`：

- `index.scss`：全局入口，`@use` 组合其余文件；页面骨架、工具类、Element Plus 覆盖（按钮渐变、输入框描边、表格浅色表头、分页、弹出菜单等）
- `variables.scss`：设计令牌（导航渐变、圆角、字号色阶、阴影、深色主题）
- `reset.scss`：轻量重置、滚动条、`scrollbar-gutter: stable`
- `mixins.scss`：`flex` / `flex-center` / `ellipsis` / `ellipsis-lines` / `panel` / `nav-pill`

约定：

- 组件统一 `<style scoped lang="scss">`，使用 BEM + `&__` 嵌套收敛重复选择器
- `vite.config.js` 通过 `css.preprocessorOptions.scss.additionalData` 全局注入 mixins，组件内可直接 `@include`
- 纵向 flex 默认 `align-items: stretch`，避免容器按内容收缩
- 宽度基线：`.app-container` 最大 1560px、左右 32px；`.page` 及其直接子块、卡片均为 100% 宽，保证各路由页面与页内区块同宽

## 后端要点

- 路由注册：`register_routers()` 只挂载 `backend/app/api/v1/routers.py` 的 `api_v1`；新增业务模块时在 `backend/app/modules/<域>/<功能>/controller.py` 定义 Router，GSI 只读模块在 `backend/app/modules_gsi/<模块>/<业务>/controller.py` 定义 Router，再到 `DOMAIN_CONTROLLERS` 登记（GSI 统一挂 `/gsi` 域），不做包扫描自动注册
- 命名口径：AI 表名 `sbp_ai_[域缩写]_[业务名]`（`system` / `kb` / `voice`），与目录、模型类一一对应，详见 `backend/app/modules/__init__.py`。当前共 **24 张**：kb 8（`chunk`/`publication`/`revision`/`parse_job`/`parse_result`/`dup_group`/`chat_session`/`chat_message`）、system 6（`api_log`/`nonce`/`idempotency`/`request_log`/`auth_cache`/`prompt_template`）、voice 8、通用向量层 2（`kb_vector_collection`/`kb_vector_embedding`，由 `core/rag/vector_store.py` 懒建，非 ORM 模型）。GSI 侧 `sbp_*` 表为外部只读表，不占 AI 模块码，按「模块名/业务名」放在 `app/modules_gsi/`，类名统一 `Sbp` 前缀（如 `SbpSysOrgModel`），共 8 张且**不计入**上述 24 张
- 域收敛：`app/modules/` 只有 `kb` / `system` / `voice` 三个正式域，无过渡域；`app/modules_gsi/` 是 GSI 外部只读包，不属于正式域、不参与 `bootstrap.py` 建表。`bootstrap.py` 按 `kb → system → voice` 顺序建表（kb 在前，因分片表含 pgvector 列，须待 vector 扩展就绪）
- 表结构初始化：启动期 `create_missing_tables` 按「**缺表才建，已存在的表一律跳过**」——不补列、不加索引、不补默认值、不改类型。因此：**新建库**由模型定义一切（含 `__table_args__` 里的表达式索引）；**存量库**的加列/加索引/改类型必须由提交者写出 `ALTER` 并随 PR 交付、在目标环境执行。删列属破坏性变更，人工确认 + 备份后手动做，没有任何脚本会自动删。规则见 `00-document/03-.../01-团队开发规范.md` **D17**（早期曾有 `upgrade_schema` 对齐脚本，v2.2 已移除）。`modules_gsi` 的模型使用独立 metadata，**完全不进入**这条建表链路；GSI 表结构由 GSI 侧 Flyway 维护
- 删除语义：基座**不做软删**（`ModelMixin` 无 `is_deleted`/`deleted_time`，`CRUDBase.delete()` 一律物理删，无 `restore()`）。需要留痕迹由领域自己用 `status` 表达（`kb_revision` 留 `archived` 墓碑、`kb_chunk` 必须物理删否则向量仍会被召回）——细节见 `00-document/03-.../01-团队开发规范.md` 规则 D19
- 契约链路：`app/modules_api/contract/` 自成一套接入层——`ContractApiRoute` 负责请求级审计，`deps.py` 的幂等守卫按 `reserve → processing → complete/release` 状态机推进，异常路径用 `asyncio.shield` 保证释放动作不被取消吞掉
- 解析生命周期：解析任务跑在请求之外，用 `async_session_factory` 自开短事务回写状态；进程重启后由 `KbParseJobService.reset_unfinished()` 把残留「解析中」批量改判 `failed`（不再有「回退待解析」语义）
- 事务出口：`db_getter` 显式三分支——正常 commit / 契约失败标记命中则 rollback / 异常 rollback 后上抛，避免契约路由吞异常导致请求级事务「错误提交」
- 异常出口：`create_app` 内注册 `handle_exception`，`CustomException` 统一转成 `{code, success, msg, data}`；`ENVIRONMENT=prod` 时不回显异常细节。未注册时所有异常都会退化成 500，前端拿不到 409/503 等可读提示
- 审计开关：`API_LOG_*`（接口审计日志，落表 `sbp_ai_system_api_log`）
- 前端托管：`app.frontend("/ui", directory=STATIC_DIR, fallback="index.html")`，支持前端 history 路由直达
- 日志：loguru 输出到控制台与 `backend/logs/`，支持按大小滚动与 gzip 归档

## 测试与质量

```bash
cd backend                 # 依赖声明与 pytest.ini 均在 backend/
uv run pytest              # 单元测试
uv run ruff check .        # 静态检查
uv run ruff format .       # 格式化
cd ../frontend && npm run build   # 前端构建校验
```

当前基线：**132 passed**。测试按被测对象分四个包（约定见 [`backend/tests/README.md`](backend/tests/README.md)）：

| 包 | 用例 | 内容 |
|---|---|---|
| `tests/core/` | 51 | 基座：模型/索引口径、CRUD 条件、事务出口、请求体缓存、向量纯函数、配置校验 |
| `tests/contract/` | 44 | 契约层：错误码、签名与拒绝原因、幂等台账、审计留痕、路由鉴权行为 |
| `tests/modules/` | 34 | 业务域：kb（解析任务/发布单元/写入链路）、system（接入追踪） |
| `tests/stages/` | 3 | 阶段验收：`m1_contract` 骨架（里程碑过完即整包复核） |

除台账查询与落库端到端用例（`requires_db`，数据库不可达时自动 skip）外，其余用 aiosqlite + monkeypatch 会话工厂或桩替换，可直接在 CI 跑。

## 部署（Docker Compose）

本仓库已内置 CI/CD 与 devops（单镜像单容器，前端随镜像构建并由 FastAPI 在 `/ui` 同源托管）：

```bash
# 一键：构建门禁 → 构建并推送镜像 → docker compose 发布
devops/scripts/00-deploy-all.sh --env=dev

# 部署机手动发布 / 回滚
devops/scripts/manual/deploy.sh prod 20261026103000
devops/scripts/manual/rollback.sh prod 20261025213000
```

- 应用运行配置唯一来源：`backend/env/.env.<env>`（部署时挂载进容器 `/home/env/`，由 `backend/app/config/setting.py` 加载）
- 数据库：PostgreSQL 17 + pgvector；自建中间件按职责拆为三个独立编排（`devops/common-server/docker-compose-postgres.yml` / `-minio.yml` / `-agent.yml`，可分别启停）：数据库编排用 `pgvector/pgvector:pg17`，首次初始化自动 `CREATE EXTENSION vector`
- 模型服务：`docker-compose-agent.yml` 编排 `ollama`（`bge-m3` 向量化，`/api/embed`）与 `rerank`（llama.cpp `--rerank`，`bge-reranker-v2-m3`，`/v1/rerank`），容器名 `service-brain-ollama` / `service-brain-rerank`，详见 [devops/README.md 6.5](./devops/README.md)
- 环境矩阵：`dev` / `test` / `uat` / `prod`；容器内固定监听 `8002`，宿主机端口由 `APP_PORT` 控制
- 流水线双入口：`.gitlab-ci.yml`（单 Job，默认关闭待启用）与 `Jenkinsfile`（手动触发）
- 详细说明（目录结构、配置与口令契约、变量清单、落地步骤）：[devops/README.md](./devops/README.md)

## 约定与注意事项

- `backend/static/` 是前端构建产物目录；`npm run build` 只清理 `assets/`、`index.html`、`favicon.svg`，保留 `backend/static/images/` 等后端演示资源
- `backend/env/` 是配置目录（不是虚拟环境），不要加入 `.gitignore`
- 默认主题色在 `index.html` 首屏脚本中预置，避免闪蓝；若浏览器 localStorage 存有旧主题色，可在「界面设置 → 恢复默认设置」后生效
- 服务端口以 `backend/main.py` 为准（8002）；`backend/env` 中的 `HOST`/`PORT` 便于外部部署时对齐
- 项目文档集中在 `00-document/`，入口见 [`00-document/README.md`](00-document/README.md)（目录划分、命名规范、文档归属判定）；文档与运行代码解耦，不参与构建与启动
