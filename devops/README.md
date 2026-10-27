# 部署与流水线说明（service_brain_agent）

| 项目     | 说明                                                                                       |
| -------- | ------------------------------------------------------------------------------------------ |
| 适用仓库 | `service_brain_agent`（前后端一体化：Vue 3 构建产物由 FastAPI 在 `/ui` 下同源托管）        |
| 文档定位 | 本仓库的 CI/CD 与 devops 落地说明：流水线双入口、单镜像构建、compose 部署、配置与口令契约  |
| 运行形态 | **docker compose 单机部署**（不涉及 Kubernetes）；一个应用容器承载前端静态资源与后端 API   |
| 基线参考 | `fastapi_admin_backend`（后端）与 `fastapi_admin_web`（前端）的 CI + devops 实践，流程骨架沿用、按单体工程简化 |

> **配置唯一来源：应用自身的多环境文件 `backend/env/.env.<env>`**。`.gitlab-ci.yml` / `Jenkinsfile` 不配置任何环境变量（含口令），唯一职责是把目标环境名传给一键脚本 `devops/scripts/00-deploy-all.sh --env=<dev|test|uat|prod>`。应用运行配置不存在任何部署侧副本或渲染产物——`backend/env/.env.<env>` 在部署时**原样 bind 挂载**进容器 `/home/env/.env.<env>`，由应用内 `pydantic-settings` 自行加载。部署侧仅保留少量非口令默认值（`devops/scripts/deploy-defaults.sh`，入库、可被同名环境变量覆盖）；镜像仓库口令依赖执行机一次性 `docker login` 预认证（或临时导出 `DOCKER_REPO_USERNAME` / `DOCKER_REPO_PASSWORD`）。

---

## 1. 方案总览

### 1.1 三段式流水线

```
build（门禁校验） → package（构建并推送镜像） → deploy（docker compose 发布）
```

三段在**同一个 Job / 同一脚本内串接**（`devops/scripts/00-deploy-all.sh --env=<环境名>`），镜像 tag 通过 `image_tag.txt` 在脚本间传递，不跨 Job 传 artifact（规避 GitLab 产物大小限制）。

| 阶段    | 实现                                                                                                  |
| ------- | ----------------------------------------------------------------------------------------------------- |
| build   | `check_app.py`：`backend/app/**` 与 `backend/main.py` 语法可解析；`backend/pyproject.toml` 直接依赖全部出现在 `backend/uv.lock`；`PYTHON_IMAGE` 与 `backend/.python-version` 版本一致；前端工程完整（`package.json` 含 `build`、`vue`/`vite` 依赖、`index.html`、`vite.config.js`、`backend/static/`）；`backend/env/.env.*` 的 `DATABASE_TYPE` 合法且 postgres 下 `VECTOR_DIM` 为正整数 |
| package | `docker build -f devops/config/Dockerfile`：node 阶段构建前端 → uv 阶段装依赖并合并产物；推送时间戳 tag（`yyyyMMddHHmmss`） |
| deploy  | `deploy-compose.sh`：确保共享网络存在 → `pull` → `up -d` → HTTP 健康探测（`/healthz`）                 |

### 1.2 为什么只需要一个容器

本工程前端（`frontend/`）在构建期把产物写入后端 `backend/static/`（`vite.config.js` 的 `build.outDir = ../backend/static`），运行期由 `app.frontend("/ui", ...)` 托管。因此：

- **无 nginx 容器、无跨容器网络调用**：页面与接口同源，`/ui` 与 `/api*` 由同一进程提供，不存在跨域与反向代理配置；
- 前后端版本天然对齐：一次构建出一个镜像，不存在「前端已发、后端未发」的错配；
- 环境差异只由挂载的 `backend/env/.env.<env>` 决定，镜像本身与环境无关（同一镜像可用于四个环境）。

> 如后续引入独立网关（TLS 终结、多域名），只需在网关层反代到 `http://<部署机IP>:<APP_PORT>`，无需改动本仓库脚本。

### 1.3 环境矩阵

| 环境 | 配置文件             | 建议发布方式                                                          | 说明                                     |
| ---- | -------------------- | --------------------------------------------------------------------- | ---------------------------------------- |
| dev  | `backend/env/.env.dev`       | CI 自动（push `develop`）或 Jenkins / Run pipeline 传 `ENV_NAME=dev`  | 开发联调机                               |
| test | `backend/env/.env.test`      | CI（Run pipeline 传 `ENV_NAME=test`）或部署机手动 `manual/deploy.sh test [TAG]` | 测试环境，独立数据库名            |
| uat  | `backend/env/.env.uat`       | 部署机手动 `manual/deploy.sh uat [TAG]`（交互确认）                   | 预发布，验证生产配置                     |
| prod | `backend/env/.env.prod`      | 部署机手动 `manual/deploy.sh prod [TAG]`（交互确认）                  | 生产，口令只在部署机维护                 |

- 发布到哪个环境由 `ENV_NAME` 决定；**发布到哪台机器由 Runner / Agent 所在节点决定**；
- 目标机器上必须有 `backend/env/.env.<env>`（缺文件脚本会报错并提示参考 `backend/env/.env.dev`）；
- 全流程串行（GitLab `resource_group` / Jenkins `disableConcurrentBuilds`），避免并发覆盖。

### 1.4 与参考基座项目的差异

| 维度       | 参考实现（backend + web 双仓库）                       | 本仓库落地                                                        |
| ---------- | ------------------------------------------------------ | ----------------------------------------------------------------- |
| 服务个数   | 后端容器 + 前端 nginx 容器，靠共享 docker 网络互通      | **单容器**：前端静态资源随镜像构建、由 FastAPI 托管                |
| 镜像构建   | 后端镜像（uv）、前端镜像（pnpm + nginx）各一个          | 一个 Dockerfile 两阶段：node 构建前端 → uv 运行时                 |
| 部署形态   | compose（dev/sit）+ Kubernetes（uat/prod）双形态        | **仅 compose**，脚本内预留形态分派点                              |
| 环境命名   | dev / sit / uat / prod                                  | **dev / test / uat / prod**（与本仓库 `backend/env/` 文件一致）           |
| 配置注入   | `backend/env/.env.<env>` 挂载进容器                     | 同左：`backend/env/.env.<env>` 挂载到 `/home/env/`                        |
| 容器端口   | 容器内固定 8001，宿主机端口由 `BACKEND_PORT` 控制       | 容器内固定 **8002**，宿主机端口由 `APP_PORT` 控制（与 `main.py` 默认一致） |
| 启动方式   | 入口脚本 `python main.py run --env=<env>`               | 入口脚本 `python -m uvicorn app:create_app --factory`（关掉热重载） |
| 中间件     | `devops/common-server`（MySQL + Redis + Milvus）        | `devops/common-server`（**PostgreSQL 17 + pgvector + Ollama / llama.cpp 模型服务**，可选用） |
| 数据与向量 | 业务库 MySQL + 向量库 Milvus 两套存储                   | **单库合流**：业务表与向量表（`kb_chunk`）同在 PostgreSQL，靠 pgvector 检索 |

---

## 2. 目录结构

```
service_brain_agent/
├── .gitlab-ci.yml                          # GitLab CI 入口：单 Job（零环境配置，只传 --env=$ENV_NAME）
├── Jenkinsfile                             # Jenkins 入口：单 stage（参数 ENV_NAME / IMAGE_TAG）
├── .dockerignore                           # 根构建上下文排除（正式构建以 Dockerfile.dockerignore 为准）
├── .gitattributes                          # *.sh 强制 LF（避免 CRLF 导致 shebang 失效）
├── .gitignore                              # 忽略 image_tag.txt、devops/**/*.env、devops/data/ 等
├── backend/                                # 后端工程：应用代码、依赖、运行目录与多环境配置
│   ├── main.py                             # 启动入口（默认 0.0.0.0:8002，本地热重载）
│   ├── app/                                # api / modules / core / config / common / utils / alembic
│   ├── env/                                # 多环境配置 .env / .env.<dev|test|uat|prod>（入库模板）
│   ├── static/                             # 前端构建产物（/ui 托管）+ 后端静态资源
│   ├── logs/  data/                        # 运行日志、SQLite 数据文件（gitignore）
│   ├── tests/                              # pytest 用例
│   ├── pyproject.toml                      # 依赖声明（uv sync / uv run 均在 backend/ 下执行）
│   ├── uv.lock                             # 依赖锁文件（必须入库，镜像按此精确还原）
│   └── pytest.ini / .python-version / banner.txt
├── frontend/                               # Vue 3 前端工程（构建产物输出到 backend/static）
└── devops/
    ├── README.md                           # 本文件
    ├── compose/
    │   ├── app.yml                         # 应用服务编排（配置 bind 挂载 + 健康检查 + 外部共享网络）
    │   └── .env.example                    # compose 手工执行变量样例（真实 .env 不入库）
    ├── common-server/                       # 可选：自建数据库与本地模型服务（常驻、与应用同外部网络）
    │   ├── docker-compose.yml              # PostgreSQL 17 + pgvector、Ollama（bge-m3）、llama.cpp rerank
    │   ├── initdb/00-init.sql              # 数据目录首次初始化：CREATE EXTENSION vector
    │   ├── volumes/                        # 数据库、Ollama 模型、rerank GGUF 持久化（gitignore，自动创建）
    │   └── .env.example                    # 取值清单（编排已内联这些值，默认不读取）
    ├── config/
    │   ├── Dockerfile                      # 两阶段镜像：node 构建前端 → uv 运行时
    │   ├── Dockerfile.dockerignore         # 该镜像的构建上下文排除清单（BuildKit 特性）
    │   └── entrypoint/00-select-env.sh     # 容器入口：校验 ENVIRONMENT 后启动服务
    └── scripts/
        ├── check_app.py                    # 构建门禁校验器（纯标准库，容器内可直接运行）
        ├── build.sh                        # 门禁执行（本地 python → 本地 python3 → 容器三级回退）
        ├── package.sh                      # 构建并推送镜像，写出 image_tag.txt
        ├── deploy-defaults.sh              # 部署侧公共默认值与环境初始化（init_deploy_env）
        ├── deploy.sh                       # 部署入口（校验配置后转 deploy-compose.sh）
        ├── deploy-compose.sh               # compose 发布 + HTTP 就绪探测
        ├── 00-deploy-all.sh                # CI 一键入口：--env=<env> 初始化后 校验 → 打包 → 部署
        └── manual/
            ├── deploy.sh                   # 部署机手动发布：<env> [IMAGE_TAG]（交互确认）
            └── rollback.sh                 # 部署机手动回滚：<env> [IMAGE_TAG]
```

---

## 3. 配置与口令契约

| 内容               | 存放位置                        | 是否入库 | 说明                                                                    |
| ------------------ | ------------------------------- | -------- | ----------------------------------------------------------------------- |
| 应用运行配置       | `backend/env/.env.<env>`                | 是（模板） | 部署时 bind 挂载进容器 `/home/env/.env.<env>`，**部署脚本不解析、不渲染**；非开发环境的口令按部署机维护，不要提交实值 |
| 部署侧非口令默认值 | `devops/scripts/deploy-defaults.sh` | 是   | 镜像仓库地址、镜像名、端口、网络名、基础镜像等；执行机可用同名环境变量覆盖 |
| compose 插值变量   | `devops/compose/.env` | 否（gitignore） | 仅手工执行应用编排时需要；CI 由脚本导出变量。`devops/common-server` 的取值已内联在编排里，不需要 `.env` |
| 镜像仓库口令       | 执行机 `~/.docker/config.json` 或 CI 变量 `DOCKER_REPO_USERNAME` / `DOCKER_REPO_PASSWORD` | **否** | 严禁写入仓库任何文件；仅 `package.sh` 推送镜像时使用 |
| 运行数据           | `devops/data/logs`、`devops/common-server/volumes/{postgres,ollama,rerank}` | 否（gitignore） | 容器与模型持久化目录，由脚本或 compose 自动创建 |

容器内只有 **一个**环境变量覆盖项：`ENVIRONMENT`（取值 `dev|test|uat|prod`）。应用 `app/config/setting.py` 读取该变量后加载 `/home/env/.env.<ENVIRONMENT>`，因此 `backend/env/.env` 里的 `ENVIRONMENT=dev` 不会影响容器行为。

> `backend/env/.env.<env>` 中的 `HOST` / `PORT` / `RELOAD` 只对**宿主机直跑**（`cd backend && python main.py`）生效：容器内由入口脚本固定监听 `0.0.0.0:8002` 并关闭热重载（`main.py` 面向本地开发，`reload=True` 不适合容器）。**改容器内端口需同步改** `devops/config/entrypoint/00-select-env.sh`、`devops/compose/app.yml` 的 healthcheck 与 Dockerfile 的 `EXPOSE`。

---

## 4. 镜像构建

### 4.1 构建门禁（`build.sh` → `check_app.py`）

任一校验失败即终止发布，避免把明显有问题的代码送进镜像：

1. `backend/app/**/*.py` 与 `backend/main.py` 语法可解析（`ast.parse`）；
2. `backend/uv.lock` 存在，且 `backend/pyproject.toml` 声明的直接依赖全部出现在锁文件（`uv sync --frozen` 才不会在镜像内失败）；
3. `PYTHON_IMAGE` 与 `backend/.python-version`（3.12）主次版本一致；
4. 前端工程完整：`frontend/package.json` 可解析、含 `build` 脚本、`vue`（dependencies）与 `vite`（devDependencies）、无 `file:` / `link:` 本地路径依赖、`index.html` / `vite.config.js` / `backend/static/` 存在。

> **前置要求：`backend/uv.lock` 必须入库**（本仓库 `.gitignore` 已移除该忽略项）。前端 lock 文件当前未入库，故镜像内使用 `npm install`；如需可复现的前端依赖，提交 `frontend/package-lock.json` 并把 Dockerfile 中的 `npm install` 改为 `npm ci`。

### 4.2 镜像结构（`devops/config/Dockerfile`）

| 阶段 | 基础镜像                        | 作用                                                                 |
| ---- | ------------------------------- | -------------------------------------------------------------------- |
| web  | `node:22-alpine`                | `npm install` + `npm run build`，产物落在 `/build/backend/static`     |
| 运行 | `astral/uv:python3.12-bookworm-slim` | `uv sync --frozen --no-dev` 装依赖 → 复制 `backend/app/`、`backend/main.py`、`backend/static/` → 合并前端产物 → 入口脚本 |

- `WORKDIR=/home`：`app/config/path_conf.py` 的 `BASE_DIR` 由代码位置推导为 `/home`（仓库内的 `backend/`），日志写 `/home/logs`（compose 挂载 `devops/data/logs`）、静态资源读 `/home/static`；
- 镜像**不含 `backend/env/`**：`Dockerfile.dockerignore` 排除，`package.sh` 构建后再断言 `/home/env` 为空、且无前端工程目录；
- 依赖源：`backend/uv.lock` 已固化镜像源地址；内网构建可传 `--build-arg PYPI_INDEX_URL=<私有源>` 与 `--build-arg NPM_REGISTRY=<私有 npm 源>`（默认 `https://registry.npmmirror.com`）。

### 4.3 镜像 tag 与推送

- tag 默认取时间戳 `yyyyMMddHHmmss`（不可变，回滚即按历史 tag 重放）；可由流水线变量 `IMAGE_TAG` 临时指定；
- 推送目标 `${DOCKER_REPO_URL}/${DOCKER_REPO_NAMESPACE}/service-brain-agent`，默认值见 `deploy-defaults.sh`（如与你的仓库不一致，导出同名变量覆盖）；
- 凭证：优先环境变量 `DOCKER_REPO_USERNAME` + `DOCKER_REPO_PASSWORD`（`--password-stdin` 登录），否则依赖执行机 `docker login` 预认证。

---

## 5. 流水线入口（双入口，零环境配置）

### 5.1 GitLab CI（`.gitlab-ci.yml`）

```yaml
variables:
  ENV_NAME: "dev"                      # 目标环境，切环境走 Run pipeline 传值
  RUNNER_TAG: "service-brain-agent"    # 与注册 Runner 的 tag 一致
  GIT_CLEAN_FLAGS: "-e backend/env/ -e devops/data/"

workflow:
  rules:
#    - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == "develop"'
#    - if: '$CI_PIPELINE_SOURCE == "web"'
    - when: never

deploy-app:
  stage: deploy
  tags: ["$RUNNER_TAG"]
  resource_group: "service-brain-agent-deploy"
  script:
    - chmod +x devops/scripts/00-deploy-all.sh
    - devops/scripts/00-deploy-all.sh --env="$ENV_NAME"
```

- **默认关闭**（`when: never`）。确认 Runner 已注册、部署机已备好 `backend/env/.env.<env>` 与 `docker login` 后，取消上方两条 `rules` 注释即可启用（push `develop` 自动发布 dev；`Run pipeline` 手动指定环境）；
- `GIT_CLEAN_FLAGS` 必须保留：Runner 默认 `git clean -ffdx` 会删掉 gitignored 的 `backend/env/` 实值文件与 `devops/data/` 运行数据。

### 5.2 Jenkins（`Jenkinsfile`）

- 参数：`ENV_NAME`（choice：dev / test / uat / prod）、`IMAGE_TAG`（可选）；
- 单 stage `deploy-app`，执行 `bash devops/scripts/00-deploy-all.sh --env=${ENV_NAME:-dev}`；
- 节点需：git、bash、docker CLI（含 `docker compose`）；推荐改为固定部署机标签 `agent { label 'service-brain-agent' }`；
- 与 GitLab CI 二选一使用，避免同一环境被两个入口并发发布。

---

## 6. 部署与回滚

### 6.1 一键脚本（CI 调用）

```bash
devops/scripts/00-deploy-all.sh --env=dev
```

执行内容：

1. `deploy-defaults.sh` → `init_deploy_env <env>`：校验环境名、导出默认值、检查 `backend/env/.env.<env>` 存在；
2. `build.sh`：构建门禁；
3. `package.sh`：`docker build` + 断言 + 推送，写 `image_tag.txt`；
4. `deploy.sh` → `deploy-compose.sh`：创建共享网络（缺失时）、创建 `devops/data/logs`、`compose pull` + `up -d --remove-orphans`，再用 `http://127.0.0.1:<APP_PORT>/healthz` 轮询就绪（最多 60s，失败打印最近 50 行日志）。

### 6.2 部署机手动发布 / 回滚

```bash
# 发布指定 tag（未传 tag 时交互输入）
devops/scripts/manual/deploy.sh prod 20261026103000

# 回滚：用历史镜像 tag 重新部署
devops/scripts/manual/rollback.sh prod 20261025213000
```

- 两者都走同一套 `deploy-defaults.sh` + `deploy.sh`，与 CI 完全一致的配置来源与发布路径；
- compose 没有 Deployment 版本历史，**回滚 = 用历史 tag 重放部署**；
- 回滚不改数据库结构：若目标版本早于最近的表结构变更，需人工确认 DDL 可回退性（本工程暂未接入 Alembic 迁移链，表结构变更需随发布单独评审）。

### 6.3 中间件与模型服务：自建数据库 / Ollama / llama.cpp（可选）

```bash
# 一次性拉起（长期运行，不随应用发布重启；取值已内联在编排里，无需 .env）
docker compose -f devops/common-server/docker-compose.yml up -d

# 若要求口令不入库：把编排里的 POSTGRES_PASSWORD 改回 ${POSTGRES_PASSWORD:?}，
# 再 cp .env.example .env 填实值（.env 已 gitignore）
```

镜像 `pgvector/pgvector:pg17` 自带 pgvector 扩展（实测 0.8.7）。`initdb/00-init.sql` 在数据目录**首次**初始化时执行
`CREATE EXTENSION IF NOT EXISTS vector`；数据卷已存在时会跳过 initdb，此时由应用启动阶段按 `VECTOR_AUTO_CREATE`
幂等补建（实现在 `app/core/vector_store.py`）。两条路径均幂等，重复执行无副作用。

自建后把 `backend/env/.env.<env>` 指向容器名即可（同一 `APP_NETWORK`）：

```ini
DATABASE_TYPE=postgres
DATABASE_HOST=service-brain-postgres
DATABASE_PORT=5432                    # 容器内直连服务端口；宿主发布端口为 39990
DATABASE_NAME=service_brain           # 须与编排里的 POSTGRES_DB 一致
DATABASE_USER=postgres
DATABASE_PASSWORD=Yidatec@2026!       # 须与编排里的 POSTGRES_PASSWORD 一致
VECTOR_ENABLE=true
VECTOR_DIM=1024                      # 须与 embedding 模型输出一致（bge-m3=1024）
VECTOR_AUTO_CREATE=true
```

> - 口令两边必须一致：编排里内联的 `POSTGRES_PASSWORD` 与 `backend/env/.env.<env>` 的 `DATABASE_PASSWORD` 必须相同；
>   若要求口令不入库，把编排改回 `${POSTGRES_PASSWORD:?}` 并用同目录 `.env`（已 gitignore）注入；
> - 端口口径：编排把容器 `5432` 发布为宿主 `39990`。容器内应用按容器名直连填 `5432`；宿主机/同网段客户端（如本机跑 `DATABASE_HOST=192.168.29.120` 的 dev）填 `39990`；
> - 生产建议为应用单独建库账号并收敛权限，但 `CREATE EXTENSION` 需要超级用户：由 DBA 在部署机初始化时执行一次，
> 应用账号只需库的 `USAGE` 与表读写权限；
> - 同一台机器跑多个环境（如 test / prod）时，`POSTGRES_DB` 与 `DATABASE_NAME` 需逐环境对齐，或各起一套编排（改容器名与 `POSTGRES_PORT`）；
> - 应用侧 `DATABASE_TYPE=postgres` 时，连接串不再拼接 `charset`（asyncpg 遇到未知 query 参数会在建连阶段直接报错）。

### 6.4 向量存储（pgvector）

切块表 `kb_chunk`：`space_id` / `doc_id` / `chunk_index` 唯一约束（重复向量化幂等覆盖）、`content` 原文、
`embedding vector(<VECTOR_DIM>)`、`meta jsonb`，索引为 HNSW + `vector_cosine_ops`（m=16, ef_construction=64）。

向量能力以服务层形式提供（`app/core/vector_store.py`），对外 HTTP 接口留给后续知识库模块接入：

| 能力 | 函数 | 说明 |
| ---- | ---- | ---- |
| 建扩展/表/索引 | `ensure_vector_store()` | 启动时幂等执行，数据库不可用时只告警不阻断启动 |
| 幂等写入 | `upsert_chunks()` | 按 (space_id, doc_id, chunk_index) 覆盖，写入前校验维度 |
| 相似度召回 | `search_chunks()` | 余弦召回，支持 `space_id` / `doc_id` 过滤、`top_k`、`min_score` |
| 按文档删除 | `delete_chunks()` | 按 `doc_id`（可选 `space_id`）清理整篇切块 |

- **分数口径**：`score = 1 - cosine_distance`，越大越相似；阈值需按实际 embedding 模型重标，不要跨模型复用；
- **embedding 由调用方生成**：本工程只做维度校验与存取，不绑定模型供应商；自建模型服务见 6.5；
- **维度不可原地变更**：`kb_chunk` 的向量列类型在进程导入期定型，`VECTOR_DIM` 改动后启动时会检测到表维度不一致并报错，
  需新建表/库并重新灌入（与第 9 节限制一致）；
- **审计口径**：向量写入属高频批量路径，后续接入 HTTP 接口时不要挂 `OperationLogRoute`，避免上千维浮点数组落审计表。

### 6.5 模型服务：Ollama 向量化 + llama.cpp 重排序（可选）

同一个 `devops/common-server/docker-compose.yml` 还编排两个模型服务，常驻运行、与应用发布解耦：

| 服务 | 容器名 | 端点 | 模型 | 镜像（锁版本） |
| ---- | ------ | ---- | ---- | -------------- |
| `ollama` | `service-brain-ollama` | `POST /api/embed` | `bge-m3`（1024 维，8K 上下文，多语言） | `ollama/ollama:0.40.11`（0.40 稳定线最新补丁） |
| `rerank` | `service-brain-rerank` | `POST /v1/rerank` | `bge-reranker-v2-m3`（Q8_0，约 600MB） | `ghcr.io/ggml-org/llama.cpp:server-b11889`（按构建号固定） |

```bash
# 首次拉起：ollama-init 拉取 bge-m3，rerank-init 下载 GGUF（均为一次性容器）
docker compose -f devops/common-server/docker-compose.yml up -d
docker compose -f devops/common-server/docker-compose.yml logs -f ollama-init rerank-init

# 模型制备失败时单独重跑（卷里已有会直接跳过）
docker compose -f devops/common-server/docker-compose.yml up ollama-init
docker compose -f devops/common-server/docker-compose.yml up rerank-init
```

应用侧走同一 `APP_NETWORK` 按容器名访问，无需发布端口（下列 curl 在应用容器内或已接入该网络的调试容器内执行）：

```bash
curl -fsS http://service-brain-ollama:11434/api/embed \
  -d '{"model":"bge-m3","input":"测试文本"}'          # 返回 1024 维向量

curl -fsS http://service-brain-rerank:8080/v1/rerank \
  -d '{"query":"问题","documents":["候选一","候选二"],"top_n":2}'
```

> - **镜像锁版本**：两个推理镜像都用具体版本 tag，不用 `latest`。Ollama 每个发布版本都有同名 tag（当前 0.40.x 稳定线最新为 `0.40.11`）；
>   llama.cpp **没有发布稳定版的机制**，官方只给浮动的 `server` / `server-cuda` 等 tag，逐次构建另打 `server-b<编号>`，
>   因此按构建号固定（当前最新镜像为 `server-b11889`）。升级方式：改 `devops/common-server/docker-compose.yml` 里对应服务的
>   `image` 后 `up -d`，再用上面的 curl 自检一次 embed 与 rerank；
> - **rerank 为什么不用 Ollama**：Ollama 只提供 embed 端点，没有 rerank（交叉编码器打分）接口，社区 reranker 模型也注明
>   需 llama.cpp 类运行时；重排序由 llama.cpp 的 `llama-server --rerank` 承担，两者都跑本地 GGUF、无需出网推理。
>   若某版本启动报 pooling 相关错误，在 `command` 中补 `--pooling rank`；
> - **维度必须对齐**：`bge-m3` 输出 1024 维，`backend/env/.env.<env>` 的 `VECTOR_DIM` 必须为 1024，否则写入时会被维度校验拒绝；
> - **CPU 即可运行**：两个模型常驻内存合计约 3GB；`RERANK_THREADS` 取宿主机逻辑核数一半，`OLLAMA_KEEP_ALIVE`（默认 24h）
>   控制空闲卸载，避免每次检索冷加载；
> - **GPU 可选**：给 `ollama` / `rerank` 加 `deploy.resources.reservations.devices`（宿主要装 NVIDIA Container Toolkit）；
>   `ollama/ollama` 镜像自带 CUDA 判断无需换 tag，`llama.cpp:server` 是 CPU 构建，GPU 需改用 `server-cuda` 类 tag；
> - **模型来源**：rerank GGUF 默认从 `hf-mirror.com` 下载（国内直连 huggingface.co 通常不通），可用 `RERANK_MODEL_URL`
>   指向 ModelScope、内网制品库或其他镜像站地址。

### 6.6 运维常用命令

```bash
# 容器状态 / 日志
docker compose -p service-brain-agent -f devops/compose/app.yml ps
docker compose -p service-brain-agent -f devops/compose/app.yml logs -f --tail=100

# 健康检查与页面
curl -fsS http://127.0.0.1:8002/healthz
# 浏览器访问：http://<部署机IP>:8002/ui

# 应用日志落盘位置（宿主）
ls -lh devops/data/logs/fastapiadmin.log

# 停止（不删数据卷/网络）
docker compose -p service-brain-agent -f devops/compose/app.yml down

# 公共中间件与模型服务（独立编排，与应用互不影响）
docker compose -f devops/common-server/docker-compose.yml ps
docker compose -f devops/common-server/docker-compose.yml down
```

---

## 7. 变量清单

| 变量                    | 来源                            | 默认值                                                     | 说明                                   |
| ----------------------- | ------------------------------- | ---------------------------------------------------------- | -------------------------------------- |
| `ENV_NAME` / `ENVIRONMENT` | CI 变量 / 脚本参数           | `dev`                                                      | 目标环境，决定挂载哪个 `backend/env/.env.<env>` |
| `IMAGE_TAG`             | CI 变量 / 环境变量              | 时间戳 `yyyyMMddHHmmss`                                    | 镜像 tag                               |
| `RUNNER_TAG`            | CI 文件                         | `service-brain-agent`                                      | GitLab Runner tag                      |
| `DOCKER_REPO_URL`       | `deploy-defaults.sh`            | 阿里云 ACR 个人版实例地址                                   | 镜像仓库地址（按实际环境覆盖）          |
| `DOCKER_REPO_NAMESPACE` | `deploy-defaults.sh`            | `yidatec-mxx`                                              | 镜像仓库命名空间                        |
| `IMAGE_NAME_APP`        | `deploy-defaults.sh`            | `${DOCKER_REPO_URL}/${DOCKER_REPO_NAMESPACE}/service-brain-agent` | 镜像全名（不含 tag）            |
| `APP_NAME_APP`          | `deploy-defaults.sh`            | `service-brain-agent`                                      | 容器名 / compose 项目名                 |
| `APP_PORT`              | `deploy-defaults.sh`            | `8002`                                                     | 宿主机暴露端口（容器内固定 8002）       |
| `APP_NETWORK`           | `deploy-defaults.sh`            | `service-brain-agent`                                      | 与中间件共用的外部 docker 网络名        |
| `PYTHON_IMAGE`          | `deploy-defaults.sh`            | `astral/uv:python3.12-bookworm-slim`                       | 运行时基础镜像 / 门禁回退容器           |
| `NODE_IMAGE`            | `deploy-defaults.sh`            | `node:22-alpine`                                           | 前端构建基础镜像                        |
| `NPM_REGISTRY`          | `deploy-defaults.sh`            | `https://registry.npmmirror.com`                           | 前端依赖源                              |
| `PYPI_INDEX_URL`        | `deploy-defaults.sh`            | 空（用 `uv.lock` 固化源）                                   | 可选：覆盖 Python 依赖源                |
| `DOCKER_REPO_USERNAME` / `DOCKER_REPO_PASSWORD` | 执行机环境变量 | 无                                                         | 可选；两者须同时提供，仅本次登录用       |

---

## 8. 落地步骤（Checklist）

1. **入库锁文件**：确认 `backend/uv.lock` 已提交（本仓库已从 `.gitignore` 移除该忽略项）；`frontend/package-lock.json` 可选。
2. **配置镜像仓库**：核对 `deploy-defaults.sh` 中 `DOCKER_REPO_URL` / `DOCKER_REPO_NAMESPACE`，或导出同名环境变量覆盖。
3. **准备部署机**：安装 docker + compose 插件；执行机一次性 `docker login <DOCKER_REPO_URL>`。
4. **准备环境文件**：在部署机放置 `backend/env/.env.<env>`（参考 `backend/env/.env.dev`，改 `DATABASE_*`、`LOGGER_LEVEL`、`DEBUG=false`、`RELOAD=false`）；生产口令不要提交入库。
5. **（可选）自建数据库与模型服务**：`devops/common-server` 拉起 PostgreSQL 17 + pgvector、Ollama（bge-m3 向量化）与 llama.cpp（bge-reranker-v2-m3 重排序），把 `DATABASE_HOST` 改为容器名即可；模型地址与拉取方式见 6.5。
6. **注册 Runner**：GitLab Runner 打 tag `service-brain-agent`（或改 CI 文件中的 `RUNNER_TAG`）；Jenkins 侧配置节点具备 docker CLI。
7. **启用流水线**：在 `.gitlab-ci.yml` 中取消 `workflow.rules` 注释；或直接用 Jenkins `Build Now` 手动发布。
8. **首轮试运行**：dev 验证 `/ui` 与 `/healthz` → test / uat / prod 逐级手动发布 → 演练一次回滚。

---

## 9. 已知限制与后续扩展

| 限制                                   | 影响                                                     | 后续做法                                                       |
| -------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------- |
| 仅 compose、单副本                     | 更新期间容器重启，服务短暂中断（数秒）                   | 需要零中断或集群化时，在 `deploy.sh` 增加 `DEPLOY_MODE=k8s` 分支 |
| 无数据库迁移链                         | 表结构变更无版本管理，回滚不覆盖 DDL                     | 接入 Alembic（`backend/app/alembic/versions` 目录已就位）并在启动前执行 `upgrade head` |
| 前端 lock 未入库                       | 同一 commit 两次构建可能得到不同前端依赖版本             | 提交 `package-lock.json` 并改 `npm ci`                          |
| 健康检查不探 DB                        | `/healthz` 只反映进程存活，数据库不可用时仍报健康        | 需要就绪探针时，新增 `/readyz` 校验 `get_db_version()`          |
| 单机 bind 挂载日志                     | 日志随宿主目录增长，需宿主侧轮转                         | 已启用 loguru 按日轮转（保留 30 天），必要时接入集中日志采集     |
| 向量维度不可原地变更                   | 改 `VECTOR_DIM` 后旧表维度不符，启动时告警、写入报错      | 新建表/库并重新灌入向量；或引入独立向量表版本号                |
| `CREATE EXTENSION` 需超级用户           | 托管数据库的普通应用账号无权建扩展                       | DBA 在实例初始化时执行一次 `CREATE EXTENSION vector`，或关闭 `VECTOR_AUTO_CREATE` |
| 向量索引构建会锁表                      | 大表上 `CREATE INDEX` 期间写入受影响                     | 数据量大时改为人工维护（`VECTOR_AUTO_CREATE=false`），按需 `CREATE INDEX CONCURRENTLY` |
