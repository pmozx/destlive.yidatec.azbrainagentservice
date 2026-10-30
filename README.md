# 智能体应用监控台（d00-fastapi-integration）

基于 **FastAPI + Vue 3** 的前后端一体化应用工程：后端提供日志审计与接口自检等基础能力，前端为与之同源部署的监控台，构建产物由 FastAPI 的 `/ui` 直接托管。

- 后端：FastAPI 工厂模式 + 异步 SQLAlchemy 2.0 + APScheduler + loguru，业务模块集中在 `app/modules`，由路由总表显式注册，同时提供 Swagger / ReDoc 接口文档
- 前端：Vue 3 + Vite 7 + Element Plus，顶部横向导航 + 白色大圆角卡片布局，酒红紫主题，支持深色模式与主题色切换
- 工程化：uv 管理 Python 依赖、npm 管理前端依赖；pytest + ruff；SCSS（BEM + mixins）

## 技术栈

| 层次 | 选型 |
| --- | --- |
| Web 框架 | FastAPI、uvicorn[standard] |
| 数据层 | SQLAlchemy 2.0（async）、asyncpg（PostgreSQL 主选）/ aiomysql / aiosqlite、alembic |
| 向量存储 | PostgreSQL + pgvector（`pgvector` Python 包 + HNSW 余弦索引，表 `sbp_ai_integration_chunk`） |
| 对象存储 | MinIO（S3 协议兼容，boto3 + path-style；桶按 `S3_AUTO_CREATE` 启动时幂等创建） |
| 文档解析 | MinerU（`mineru-open-sdk`）：PDF / Office / 图片 → Markdown + 版面 JSON（页码溯源）；纯文本/HTML 走内置解析器 |
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
│   │   ├── modules/             # 业务模块：域/功能两级，表名统一前缀 sbp_ai_[域缩写]_
│   │   │   ├── system/api_log/ # 系统管理：接口审计日志（sbp_ai_system_api_log）
│   │   │   ├── kb/             # 知识库管理：当前暂空，分片与知识空间定稿后迁入（表名改 sbp_ai_kb_*）
│   │   │   ├── voice/          # 语音质检管理：二期预留（sbp_ai_voice_*）
│   │   │   └── integration/    # 待集成区：LLM/向量化/重排/MinerU 客户端、解析线程池、文档流水线、自检端点
│   │   │       ├── clients/    # 集成层客户端：LLM / 向量化 / 重排 / MinerU / S3 对象存储
│   │   │       ├── runtime/    # 阻塞任务线程池：解析线程池 + 对象存储 IO 线程池
│   │   │       ├── space/      # 集成期知识空间与文档（sbp_ai_integration_space / _document，上传 + 线程池解析）
│   │   │       └── chunk/      # 集成期分片与向量存储（sbp_ai_integration_chunk，表结构未定稿，带前端 CRUD）
│   │   ├── core/                # 数据库、依赖、异常、日志、审计路由类、校验器
│   │   ├── config/              # 路径常量 + Settings（多环境）
│   │   ├── common/              # 常量、枚举、响应体、请求体
│   │   ├── utils/               # banner、密码、通用工具
│   │   └── alembic/             # 数据库迁移
│   ├── env/                     # 多环境配置：.env / .env.dev / .env.test / .env.prod ...
│   ├── static/                  # 前端构建产物（/ui 托管）与后端静态资源
│   ├── tests/                   # pytest 用例
│   ├── logs/  data/             # 运行日志、SQLite 数据文件
│   ├── pyproject.toml / uv.lock # 依赖声明与锁文件（uv sync / uv run 均在 backend/ 下执行）
│   └── pytest.ini / .python-version / banner.txt
├── frontend/                    # Vue 3 前端工程
│   ├── src/
│   │   ├── api/                 # 接口封装（module_system / module_monitor / module_integration）
│   │   ├── layouts/             # 顶栏、页签、设置抽屉、布局骨架
│   │   ├── views/               # dashboard、module_system、module_monitor、module_integration、error
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
VECTOR_AUTO_CREATE=true          # 启动时幂等建扩展、分片表与 HNSW 索引
```

向量存储（`backend/app/modules/integration/chunk/vector_store.py`）：启动时幂等建 `vector` 扩展、分片表与 HNSW 索引，为后续 RAG 能力预留。分片表结构尚未定稿（后续可能拆原文/向量表、补分类与权重列），因此暂寄在待集成区并带完整 CRUD 接口，定稿后整体迁入知识库域（表名改 `sbp_ai_kb_chunk`，路由前缀改 `/kb/chunk`）。

集成期分片接口（`/api/v1/integration/chunk`，审计路由 `ApiLogRoute`）：

| 方法与路径 | 说明 |
| --- | --- |
| `GET /list` | 分页查询（`space_id` / `doc_id` / `content` 模糊，`page_no` 精确），不返回 embedding 列 |
| `GET /detail/{id}` | 单条详情 |
| `POST /create` | 新增分片：先查 `(space_id, doc_id, chunk_index)` 重复（409），再调 embedding 服务生成向量并校验维度 |
| `PUT /update/{id}` | 更新；仅当 `content` 变化才重算向量，改页码/meta 不触发向量化 |
| `DELETE /delete` | 批量物理删除（body 为 id 数组） |
| `POST /search` | 余弦相似度检索（`query` / `space_id` / `doc_id` / `top_k` / `min_score`），`score = 1 - cosine_distance` |

向量由服务端调用 `EmbeddingClient` 生成，前端不传 embedding，避免脏向量；`VECTOR_ENABLE=false` 或未接 embedding 服务时，新增/搜索返回 503 并提示缺失变量。

### 对象存储（MinIO / S3 兼容）

文档原件与解析产物落对象存储，统一走 S3 协议（客户端：`backend/app/modules/integration/clients/s3.py`，boto3 + 阻塞 IO 线程池）；
本地/私有化部署由 `devops/common-server` 的 `minio` 服务提供，客户端能力不依赖 MinIO 私有 API，日后换云厂商只改 endpoint 与凭据。

```ini
S3_ENABLE=true
S3_ENDPOINT=http://192.168.29.120:39900   # 宿主进程直连；容器内改 http://service-brain-minio:9000
S3_ACCESS_KEY=service-brain
S3_SECRET_KEY=...                         # 密钥只写 env，不入库、不进代码
S3_BUCKET=service-brain-dev               # 多环境共用一套 MinIO 时按桶名隔离
S3_ADDRESSING_STYLE=path                  # MinIO 必须 path-style，virtual 会拼子域名直接失败
S3_AUTO_CREATE=true                       # 启动时幂等建桶（与建向量表/审计表同一策略）
IO_THREAD_POOL_SIZE=4                     # 对象存储上传/下载的线程池并发
```

客户端能力：`ensure_bucket()`（幂等建桶）、`put_object()` / `put_file()`（流式上传，不整份进内存）、`get_object()`、
`object_exists()`、`delete_object()`、`list_keys()`（自动翻页）、`delete_prefix()`（按目录清理；批量接口被 S3 兼容实现拒掉时逐个删）、
`presigned_url()`（预签名下载，host 即 `S3_ENDPOINT`）、`build_key()`
（`{前缀}/{日期}/{uuid}_{文件名}`，避免同名覆盖）。

上传/下载全部在独立 IO 线程池里执行，不占事件循环；与解析线程池分开，避免分钟级文档解析把上传堵在队列里。
配置缺项时调用报 503 并点明该填哪个变量；`GET /api/v1/integration/probe?target=s3` 会真实建桶、写入探针对象、读回比对后删除。
更完整的编排口径（镜像、端口、凭据与运维命令）见 [devops/README.md 6.6](./devops/README.md)。

### 集成期知识空间（上传 → 线程池解析 → 分片入库）

表结构未定稿，先暂寄待集成区：`sbp_ai_integration_space`（空间）、`sbp_ai_integration_document`（文档），
启动时由 `ensure_space_tables()` 幂等建表，并把上次进程中断留下的「解析中」改回「待解析」。

```ini
SPACE_UPLOAD_MAX_FILES=10      # 单次批量上传文件数上限
SPACE_UPLOAD_MAX_SIZE_MB=50    # 单个文件大小上限，超限逐份报错、不回滚整批
```

口径：

1. 空间 `code` 就是分片表的 `space_id`，文档 `doc_id` 就是分片表的 `doc_id` —— 空间文档解析完可直接在「文档分片」页按这两个键查到分片；代价是 `code` 创建后不可修改；
2. 上传只做「写对象存储 + 落库为待解析」，不自动解析：解析是分钟级动作，必须由用户显式提交，否则一个误传的大文件就会占满解析线程池；
3. 解析任务走 `space/task.py` 的进程内任务登记表 + 独立会话：同一文档不并行解析、状态机（待解析 → 解析中 → 已完成/失败）只在任务里推进、关停时统一取消；
4. 并发由信号量限制在 `PARSE_THREAD_POOL_SIZE`，解析/分片在解析线程池、向量化走异步 HTTP、写库用 `upsert_chunks` 按文档幂等覆盖（重跑不产生重复分片，重跑后条数变少会清掉多出来的尾块）；
5. 删除是级联的：删文档/删空间都会连带删分片、对象存储原件与解析产物（`parsed/` 整目录），失败只告警不阻断；
6. 解析产物键布局照搬知识库口径：`{S3_KEY_PREFIX}/spaces/{space}/{doc_id}/parsed/{full.md, content_list.json, images/*}`。键全部由 `space_id + doc_id` 推导，不新增文档表列；
7. 产物落盘失败只告警、不影响分片入库（解析成功就不该记成失败）；反过来，文档在解析途中被删除时，任务回写状态会发现文档已不存在，随即撤销本次写出的分片与产物，不留无主数据。

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/integration/space/list` | 空间分页（附文档/分片统计） |
| `GET /api/v1/integration/space/detail/{id}` | 空间详情 |
| `POST /api/v1/integration/space/create` | 新建空间（`code` 唯一且不可变） |
| `PUT /api/v1/integration/space/update/{id}` | 更新名称/说明 |
| `DELETE /api/v1/integration/space/delete` | 删除空间（级联文档、分片、原件） |
| `GET /api/v1/integration/space/document/list` | 文档分页（列表不加载 `preview`） |
| `GET /api/v1/integration/space/document/detail/{id}` | 文档详情（含解析正文预览与失败原因） |
| `POST /api/v1/integration/space/document/upload` | multipart 批量上传（`space_id` + `files`） |
| `POST /api/v1/integration/space/document/parse` | 提交解析（`document_ids` 留空 = 空间全部待解析/失败，`force=true` 含已完成） |
| `GET /api/v1/integration/space/document/markdown/{doc_id}` | 预览解析产物：Markdown 全文（图片引用重写为图片读取接口）+ 图片数 + 是否留存版面 JSON |
| `GET /api/v1/integration/space/document/asset/{doc_id}/images/{name}` | 读取产物图片（带 `nosniff` 与 CSP sandbox；GET 不进审计日志） |
| `DELETE /api/v1/integration/space/document/delete` | 删除文档（级联分片、原件与解析产物） |

### 模型服务（待集成区 `app/modules/integration`）

集成本地/私有化模型时按需填写，缺省留空即视为“未接入”，调用时报 503 并提示该填哪个变量，不做静默降级：

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

解析器分工（`app/modules/integration/pipeline/`）：

| 文件类型 | 处理方式 |
| --- | --- |
| `.pdf` `.doc(x)` `.ppt(x)` `.xls(x)` `.png/.jpg/.jpeg/.bmp/.tif/.tiff/.webp` | MinerU（启动时由 `install_mineru_parser()` 按 `MINERU_ENABLE` 注册，`prepend` 优先） |
| `.txt` `.md` `.csv` `.json` `.yaml` `.log` | 内置纯文本解析器（UTF-8 BOM → UTF-8 → GB18030），不浪费一次上传 |
| `.html` `.htm` | 内置 HTML 解析器（剥离标签与 script/style） |

MinerU 返回的 `content_list` 会转成「文本 → 页码」映射，分片时用单调游标按文本前缀回填 `page_no`（一级匹配不上退到窗口匹配、仍不中沿用上一块页码）。
两件事要注意：

1. **flash 与 precise 差别很大**：未配 `MINERU_API_TOKEN` 走 flash，只回 Markdown —— 既没有 `content_list`（页码只能留空），也没有图片（落不了 `content_list.json` / `images/`）。要页码与完整产物必须配 token 走 precise；
2. MinerU 的提交+轮询是分钟级阻塞调用，一律在解析线程池里执行，不占事件循环。

解析完成后 `full.md`、`content_list.json` 与图片一并落对象存储（键布局见上一节第 6 条），前端详情弹窗的「解析产物预览」直接读桶里的 Markdown 渲染，图片走 `/document/asset/...` 接口；历史文档需在页面点「强制重新解析」才会补上页码与产物。

联调前先跑自检：`GET /api/v1/integration/probe?target=all`（可选 `llm`/`embedding`/`rerank`/`mineru`/`pipeline`），逐项返回 `configured`（是否已配置）、`ok`、耗时与错误原因；`mineru` 与 `pipeline` 项只做静态检查（SDK 是否安装、模式、解析器是否接管后缀、分片参数、向量存储可用性），不触达外部服务。

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
| `/monitor/endpoint` | 接口自检 | 接口清单 + 逐个探测，JSON 展示响应与耗时 |
| `/integration/space` | 知识空间 | 空间增删改（`code` 即分片 `space_id`）、批量上传文档到对象存储、提交解析（选中/全部/强制）、按状态轮询进度、详情看解析预览与失败原因 |
| `/integration/probe` | 集成自检 | LLM / 向量化 / 重排 / 对象存储 / MinerU / 流水线卡片式自检，展示是否已配置、耗时与失败原因 |
| `/integration/chunk` | 文档分片 | 分片分页查询、新增/编辑/批量删除（meta 用 JSON 录入）、余弦相似度检索 |

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

- 路由注册：`register_routers()` 只挂载 `backend/app/api/v1/routers.py` 的 `api_v1`；新增业务模块时在 `backend/app/modules/<域>/<功能>/controller.py` 定义 Router，再到 `DOMAIN_CONTROLLERS` 登记，不做包扫描自动注册
- 命名口径：表名 `sbp_ai_[域缩写]_[业务名]`（`system` / `kb` / `voice`），与目录、模型类一一对应，详见 `backend/app/modules/__init__.py`
- 待集成区：`app/modules/integration` 不是业务域，承接还没定稿的能力（模型客户端、S3 对象存储、MinerU 解析、解析/IO 线程池、文档流水线、知识空间、分片与向量存储）。依赖方向单向——integration 可引用业务域，业务域禁止 import integration；某个能力调通后整体搬进目标业务模块并删除本域实现，不必留转发薄壳。当前提供 `GET /api/v1/integration/probe` 自检端点、`/api/v1/integration/space` 知识空间与文档上传/解析、`/api/v1/integration/chunk` 分片 CRUD
- 解析生命周期：解析任务跑在请求之外，用 `async_session_factory` 自开短事务回写状态；启动时 `reset_interrupted_documents()` 把残留「解析中」改回「待解析」，关停时先 `cancel_all()` 再关 IO/解析线程池，避免半截分片写库
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
- 仓库根目录 `00-document/`、`01-document-AI Agent/` 为配套学习笔记，与运行无关
