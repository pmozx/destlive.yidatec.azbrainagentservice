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
| 中间件     | `devops/common-server`（MySQL + Redis + Milvus）        | `devops/common-server`（**PostgreSQL 17 + pgvector + MinIO + Ollama / llama.cpp 模型服务**，可选用） |
| 数据与向量 | 业务库 MySQL + 向量库 Milvus 两套存储                   | **单库合流**：业务表与向量表（`sbp_ai_kb_chunk`，含 `embedding vector` 列）同在 PostgreSQL，靠 pgvector + HNSW 检索 |
| 对象存储   | 业务库 MySQL + 向量库 Milvus                            | ~~**MinIO（S3 协议）**：文档原件与解析产物落桶~~ **当前应用侧不使用**（客户端已随 integration 域删除，编排保留待重建） |

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
│   ├── app/                                # api / modules / core / config / common / utils
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
    ├── brain-agent-server/                  # 应用服务编排（自包含，不读 .env）
    │   └── docker-compose.yml              # 应用编排：配置 bind 挂载 + 健康检查 + 外部共享网络
    ├── common-server/                       # 可选：自建数据库与本地模型服务（常驻、与应用同外部网络）
    │   ├── docker-compose-postgres.yml     # PostgreSQL 17 + pgvector（独立编排）
    │   ├── docker-compose-minio.yml        # MinIO（S3 兼容对象存储，独立编排）
    │   ├── docker-compose-agent.yml        # Ollama（bge-m3 向量化）+ llama.cpp rerank（独立编排）
    │   ├── initdb/00-init.sql              # 数据目录首次初始化：CREATE EXTENSION vector
    │   └── volumes/                        # 数据库、MinIO、Ollama 模型、rerank GGUF 持久化（gitignore，自动创建）
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
| compose 插值变量   | 无（不读 `.env`）                    | —     | 应用编排与 `common-server` 编排都自包含：变量由部署脚本导出或取编排内默认值，仓库不提供 `.env.example` |
| 镜像仓库口令       | 执行机 `~/.docker/config.json` 或 CI 变量 `DOCKER_REPO_USERNAME` / `DOCKER_REPO_PASSWORD` | **否** | 严禁写入仓库任何文件；仅 `package.sh` 推送镜像时使用 |
| 运行数据           | `devops/data/logs`、`devops/common-server/volumes/{postgres,minio,ollama,rerank}` | 否（gitignore） | 容器、对象存储与模型持久化目录，由脚本或 compose 自动创建 |

容器内只有 **一个**环境变量覆盖项：`ENVIRONMENT`（取值 `dev|test|uat|prod`）。应用 `app/config/setting.py` 读取该变量后加载 `/home/env/.env.<ENVIRONMENT>`，因此 `backend/env/.env` 里的 `ENVIRONMENT=dev` 不会影响容器行为。

> `backend/env/.env.<env>` 中的 `HOST` / `PORT` / `RELOAD` 只对**宿主机直跑**（`cd backend && python main.py`）生效：容器内由入口脚本固定监听 `0.0.0.0:8002` 并关闭热重载（`main.py` 面向本地开发，`reload=True` 不适合容器）。**改容器内端口需同步改** `devops/config/entrypoint/00-select-env.sh`、`devops/brain-agent-server/docker-compose.yml` 的 healthcheck 与 Dockerfile 的 `EXPOSE`。

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
- 回滚不改数据库结构：若目标版本早于最近的表结构变更，需人工确认 DDL 可回退性（本工程不使用数据库迁移，表结构变更需随发布单独评审）。

### 6.3 中间件与模型服务：自建数据库 / MinIO / Ollama / llama.cpp（可选）

公共中间件按职责拆成三个独立编排，可分别启停、升级、备份，互不影响：
`docker-compose-postgres.yml`（数据库）、`docker-compose-minio.yml`（对象存储）、
`docker-compose-agent.yml`（向量化 + 重排序）。三者都接入同一个外部共享网络 `service-brain-agent`。

```bash
# 按需分别拉起（长期运行，不随应用发布重启；取值已内联在编排里，无需 .env）
docker compose -f devops/common-server/docker-compose-postgres.yml up -d   # 数据库
docker compose -f devops/common-server/docker-compose-minio.yml up -d      # 对象存储 S3
docker compose -f devops/common-server/docker-compose-agent.yml up -d      # 向量化 + 重排序

# 若要求口令不入库：把 postgres 编排里的 POSTGRES_PASSWORD 改回 ${POSTGRES_PASSWORD:?}，
# 再在同目录新建 .env 填实值（.env 已 gitignore）
```

> **从旧单文件编排迁移**：若部署机此前用 `devops/common-server/docker-compose.yml`（已拆为上面三个文件）起过服务，容器名已被占用，
> 直接按新文件 `up -d` 会报名称冲突。先按容器名删除旧容器（数据在 `volumes/` 绑定目录里，不受影响），再按上面三条命令拉起：
>
> ```bash
> docker rm -f service-brain-postgres service-brain-minio service-brain-ollama \
>   service-brain-ollama-init service-brain-rerank-init service-brain-rerank
> ```

镜像 `pgvector/pgvector:pg17` 自带 pgvector 扩展（实测 0.8.7）。`initdb/00-init.sql` 在数据目录**首次**初始化时执行
`CREATE EXTENSION IF NOT EXISTS vector`；数据卷已存在时会跳过 initdb，此时由应用启动阶段按 `VECTOR_AUTO_CREATE`
幂等补建（实现在 `app/core/table_bootstrap.py` 的 `ensure_pgvector_extension()`，lifespan 第一顺位执行）。两条路径均幂等，重复执行无副作用。
扩展就绪后才轮到各域建表（`kb → system → voice`），否则 `sbp_ai_kb_chunk` 的 `vector` 列与 HNSW 索引会建失败。

自建后把 `backend/env/.env.<env>` 指向容器名即可（与中间件同在 `service-brain-agent` 网络）：

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

分片表 `sbp_ai_kb_chunk`（kb 域正式表，由 `app/modules/kb/bootstrap.py` 在建扩展后创建）：
`publication_id` / `record_id` / `revision` / `seq` 定位切片，`content` 原文、`embedding vector(<VECTOR_DIM>)`、
`content_hash` 去重、`status` 生效标记，索引为 HNSW + `vector_cosine_ops`（m=16, ef_construction=64）。

写入链路：契约 #03/#05 → `KbEntryService._build_chunk_rows` → `EmbeddingClient.embed_texts` 批量向量化 →
`validate_embedding` 校验维度 → `KbChunkCRUD.insert_many` 落库。**维度不符或条数不一致整批拒绝，不留半截数据**。

> **两套向量表的分工**（务必分清，不要两套并存写同一份数据）：
>
> | 表 | 归属 | 现状 |
> | --- | --- | --- |
> | `sbp_ai_kb_chunk` | kb 域业务表 | 契约 #03/#05 的**唯一写入落点**，带完整业务字段（`publication_id`/`revision`/`seq` 等） |
> | `sbp_ai_kb_vector_collection`<br>`sbp_ai_kb_vector_embedding` | `app/core/rag/vector_store.py` 的通用向量层 | **无消费方**，仅 `validate_embedding` 被 `kb/publication/service.py` 复用；表由首次读写懒建，也可显式调 `ensure_vector_store(collection)` 预建 |
>
> 通用向量层不 import 任何业务模型：一套表 + 按 collection 名逻辑隔离，业务字段全部落在 `cmetadata`（JSONB，
> `jsonb_path_ops` GIN 索引），向量索引为 HNSW + `vector_cosine_ops`（m=16, ef_construction=64）。
>
> **为什么不用 langchain-postgres 的 `PGVector`**：它把表名硬编码成 `langchain_pg_collection` /
> `langchain_pg_embedding`，构造参数无法覆盖，与「表名必须 `sbp_ai_` 开头」的强规范冲突。2026-11-02 起该依赖已移除，
> 改为 SQLAlchemy Core 直接写 SQL（表名、索引名、维度全部本模块自持），库里原 `langchain_pg_*` 两张表已删除。

- **分数口径**：`score = 1 - cosine_distance`，越大越相似；阈值需按实际 embedding 模型重标，不要跨模型复用；
- **embedding 由服务端生成**：本工程只做维度校验与存取，不绑定模型供应商；自建模型服务见 6.5；
- **维度不可原地变更**：`sbp_ai_kb_chunk.embedding` 与 `sbp_ai_kb_vector_embedding.embedding` 的列类型在进程导入期定型，
  `VECTOR_DIM` 改动后启动时会检测到维度不一致并报错，需新建表/库并重新灌入（与第 9 节限制一致）；
- **审计口径**：向量写入属高频批量路径，批量写入函数不挂 `ApiLogRoute`；只有契约入口的请求级审计。
- **表结构变更**：本工程不使用迁移工具，`bootstrap.py` 只 `create` 不 `alter`；改列需手工 DDL + 发布评审。

### 6.5 模型服务：Ollama 向量化 + llama.cpp 重排序（可选）

`devops/common-server/docker-compose-agent.yml` 编排两个模型服务，常驻运行、与应用发布解耦：

| 服务 | 容器名 | 端点 | 模型 | 镜像（锁版本） |
| ---- | ------ | ---- | ---- | -------------- |
| `ollama` | `service-brain-ollama` | `POST /api/embed` | `bge-m3`（1024 维，8K 上下文，多语言） | `ollama/ollama:0.40.11`（0.40 稳定线最新补丁） |
| `rerank` | `service-brain-rerank` | `POST /v1/rerank` | `bge-reranker-v2-m3`（Q8_0，约 600MB） | `ghcr.io/ggml-org/llama.cpp:server-b11889`（按构建号固定） |

```bash
# 首次拉起：ollama-init 拉取 bge-m3，rerank-init 下载 GGUF（均为一次性容器）
docker compose -f devops/common-server/docker-compose-agent.yml up -d
docker compose -f devops/common-server/docker-compose-agent.yml logs -f ollama-init rerank-init

# 模型制备失败时单独重跑（卷里已有会直接跳过）
docker compose -f devops/common-server/docker-compose-agent.yml up ollama-init
docker compose -f devops/common-server/docker-compose-agent.yml up rerank-init
```

应用侧走同一网络 `service-brain-agent` 按容器名访问，无需发布端口（下列 curl 在应用容器内或已接入该网络的调试容器内执行）：

```bash
curl -fsS http://service-brain-ollama:11434/api/embed \
  -d '{"model":"bge-m3","input":"测试文本"}'          # 返回 1024 维向量

curl -fsS http://service-brain-rerank:8001/v1/rerank \
  -d '{"query":"问题","documents":["候选一","候选二"],"top_n":2}'
```

> - **镜像锁版本**：两个推理镜像都用具体版本 tag，不用 `latest`。Ollama 每个发布版本都有同名 tag（当前 0.40.x 稳定线最新为 `0.40.11`）；
>   llama.cpp **没有发布稳定版的机制**，官方只给浮动的 `server` / `server-cuda` 等 tag，逐次构建另打 `server-b<编号>`，
>   因此按构建号固定（当前最新镜像为 `server-b11889`）。升级方式：改 `devops/common-server/docker-compose-agent.yml` 里对应服务的
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

### 6.6 对象存储：MinIO（S3 兼容，可选，**应用侧当前不使用**）

> **状态变更**：`S3StorageClient` 已随 `integration_kb` 域删除，应用代码中不再有任何 S3 实现，契约 #01 改为 `file_url` 直拉。
> 本节的编排仍保留在仓库内供文件上传能力重建时复用，但**当前发布无需拉起 MinIO**，`S3_*` 配置改了也不产生效果。

`devops/common-server/docker-compose-minio.yml` 提供 S3 兼容对象存储（原用于存放文档原件与解析产物）：

| 项 | 值 |
| --- | --- |
| 镜像 | `minio/minio:RELEASE.2024-12-18T13-15-44Z`（锁 RELEASE，不用 latest） |
| 容器名 | `service-brain-minio` |
| S3 API | 容器内 `9000`，宿主发布 `39900` |
| 管理控制台 | 容器内 `9001`，宿主发布 `39901` |
| 数据卷 | `devops/common-server/volumes/minio/data`（gitignore） |
| 根账号 | 内联在编排里（与数据库同口令），见 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |

```bash
# 单独拉起对象存储
docker compose -f devops/common-server/docker-compose-minio.yml up -d
# 控制台：http://<部署机IP>:39901（账号见编排）
```

应用侧配置（`backend/env/.env.<env>`）：

```ini
S3_ENABLE=true
S3_ENDPOINT=http://service-brain-minio:9000    # 容器内按容器名；宿主进程改 http://<IP>:39900
S3_ACCESS_KEY=service-brain
S3_SECRET_KEY=...
S3_BUCKET=service-brain                        # dev/test/uat 各建一个桶隔离
S3_ADDRESSING_STYLE=path                       # MinIO 必须 path-style
S3_AUTO_CREATE=true                            # 启动时幂等建桶
```

> - **桶由应用建**：启动时 `head_bucket → create_bucket`（`S3_AUTO_CREATE`），与建向量表/审计表同一策略，重复执行无副作用；
>   因此编排里不塞 `minio-init` 容器，少维护一个 `mc` 镜像 tag；
> - **寻址风格**：务必保持 `path`。`virtual` 会把桶名拼成 `service-brain-minio.<域名>`，内网无此 DNS 记录，所有请求直接失败；
> - **凭据**：编排内联的是 root 账号，仅适合联调；正式环境用 `mc admin user add` 建最小权限子账号（只给目标桶读写）并写入 env；
> - **自检**：「系统集成 → 集成自检」的「对象存储」卡片会真实建桶、写入探针对象、读回比对后删除，
>   一次暴露凭据错、权限不足、寻址配错与网络不通（临时对象在 `_self_check/` 前缀下，读完即删）；
> - **预签名链接**：链接 host 就是 `S3_ENDPOINT`；要让浏览器直接打开，endpoint 必须是对外可达地址，
>   容器内按容器名填写时外部打不开；
> - **桶内布局**：原件 `spaces/{空间code}/{doc_id}/{原文件名}`，解析产物 `spaces/{空间code}/{doc_id}/parsed/{full.md, content_list.json, images/*}`（`S3_KEY_PREFIX` 非空时整体挂在它下面）。
>   键全部由空间与文档标识推导，库里不存产物键，因此删文档时按 `parsed/` 前缀整目录清理；
> - **批量删除的兼容坑**：早期/当前版本的 MinIO 对 `DeleteObjects` 强制要求 `Content-MD5`，而新版 botocore 改发 CRC32 校验头，
>   批量接口会回 `MissingContentMD5`；客户端已退化为逐个 `delete_object`，日志里会有一条 warning（属预期降级，不是故障）；
> - **镜像名**：是 `minio/minio`（不是 `milvusdb/minio`），Milvus 官方编排引用的也是 `minio/minio` 的同一 RELEASE 构建。

### 6.7 知识空间表（**已删除**）

原 `sbp_ai_integration_space`（知识空间）与 `sbp_ai_integration_document`（空间文档）两张表、`ensure_space_tables()` 建表逻辑、
`space/task.py` 进程内任务登记表，均随 `integration_kb` 模块**整体删除**。dev 环境已核验：`public` schema 中**不存在任何名字含
`integration` 的表**，`sbp_ai_*` 恰为 22 张（kb 8 / system 6 / voice 8），与代码模型一一对应。

原「上传 → 写桶 → 落库待解析 → 显式提交解析」的编排队列、级联删除、解析产物落桶、按 `space_id`/`doc_id` 查分片等口径**全部作废**。
解析任务的新落点是 `sbp_ai_kb_parse_job` + `sbp_ai_kb_parse_result`，状态机由 `KbParseJobService` 推进，进程重启后残留「解析中」批量改判 `failed`
（`KbParseJobService.reset_unfinished()`），不再回退为「待解析」。

### 6.8 运维常用命令

```bash
# 容器状态 / 日志
docker compose -p service-brain-agent -f devops/brain-agent-server/docker-compose.yml ps
docker compose -p service-brain-agent -f devops/brain-agent-server/docker-compose.yml logs -f --tail=100

# 健康检查与页面
curl -fsS http://127.0.0.1:8002/healthz
# 浏览器访问：http://<部署机IP>:8002/ui

# 应用日志落盘位置（宿主）
ls -lh devops/data/logs/fastapiadmin.log

# 停止（不删数据卷/网络）
docker compose -p service-brain-agent -f devops/brain-agent-server/docker-compose.yml down

# 公共中间件与模型服务（三个独立编排，与应用互不影响）
docker compose -f devops/common-server/docker-compose-postgres.yml ps
docker compose -f devops/common-server/docker-compose-minio.yml ps
docker compose -f devops/common-server/docker-compose-agent.yml ps

# 逐个停止（不删 volumes 数据目录；按需只停其中一个）
docker compose -f devops/common-server/docker-compose-agent.yml down
docker compose -f devops/common-server/docker-compose-minio.yml down
docker compose -f devops/common-server/docker-compose-postgres.yml down
```

### 6.9 独立服务器部署（只上传 devops）

适用场景：目标机只装了 docker 与 docker compose（无 git、无 Python、无源码构建链），镜像已由 Jenkins 构建并推送，手上只有构建输出的 `image_tag`。

前置：`docker compose version` 能输出版本（需 compose v2 插件，v1 的 `docker-compose` 不适用）、宿主有 `curl`、当前用户能执行 docker 命令、可访问镜像仓库。

#### 目录约定（脚本按此推导，不能随意改层级）

| 内容 | 位置 | 原因 |
| --- | --- | --- |
| 编排与脚本 | `~/service_brain_agent/devops/` | 脚本用自身位置推导项目根：`devops/scripts/../..` = `~/service_brain_agent` |
| 应用配置 | `~/service_brain_agent/backend/env/.env.<env>` | `deploy-defaults.sh` 校验其存在；compose 从 `devops/brain-agent-server/` 上两级挂载同一路径 |
| 日志 | `~/service_brain_agent/devops/data/logs` | 首次部署自动创建 |
| 中间件数据（可选） | `~/service_brain_agent/devops/common-server/volumes/` | 首次启动自动创建 |

可见 `backend/` 下**只需要一个 env 文件**，不需要源码：

```text
~/service_brain_agent/
├── backend/env/.env.prod          # 唯一需要从仓库外带进来的文件
└── devops/
    ├── brain-agent-server/docker-compose.yml
    ├── common-server/              # 同机自建数据库/对象存储/模型时才需要
    │   ├── docker-compose-postgres.yml
    │   ├── docker-compose-minio.yml
    │   ├── docker-compose-agent.yml
    │   └── initdb/00-init.sql
    └── scripts/
        ├── deploy-defaults.sh
        ├── deploy.sh
        ├── deploy-compose.sh
        └── manual/{deploy.sh,rollback.sh}
```

`devops/` 其余内容（`config/` 镜像构建文件、`build.sh` / `package.sh`、`check_app.py`）是构建侧文件，运行机不需要。

#### 一次性准备

Windows 侧：在资源管理器中**右键 `devops` 文件夹本身 → 压缩为 ZIP**，得到 `devops.zip`（zip 内顶层须为 `devops/`，不能进入文件夹全选压缩），再通过远程桌面拖拽、SFTP 客户端、堡垒机或 U 盘等任意方式放到服务器 `~/` 下。

Linux 侧执行：

```bash
# 1) 解压（zip 内顶层为 devops/）
mkdir -p ~/service_brain_agent
cd ~/service_brain_agent
unzip -o ~/devops.zip

# 2) 去掉 CRLF（Windows 打包必带），并补执行权限
find ~/service_brain_agent/devops -name '*.sh' -exec sed -i 's/\r$//' {} +
chmod +x ~/service_brain_agent/devops/scripts/*.sh ~/service_brain_agent/devops/scripts/manual/*.sh

# 3) 放置应用配置（从仓库 backend/env/.env.prod 拷一份，按本机情况填）
mkdir -p ~/service_brain_agent/backend/env
vi ~/service_brain_agent/backend/env/.env.prod
#   同机自建中间件时写容器名：DATABASE_HOST=service-brain-postgres、
#   S3_ENDPOINT=http://service-brain-minio:9000、OLLAMA_BASE_URL=http://service-brain-ollama:11434

# 4) 镜像仓库登录（ACR 个人版需先 docker login）
docker login <DOCKER_REPO_URL>
docker login crpi-7nbh1cvsjd338a37.cn-qingdao.personal.cr.aliyuncs.com
# qwerasd5
# 15513974562_Meng

# 5) （可选）同机自建中间件：先起中间件，再起应用
cd ~/service_brain_agent
for f in postgres minio agent; do
    docker compose -f devops/common-server/docker-compose-$f.yml up -d
done

# test 服务器仅部署 Agent 私有化相关服务
docker compose -f devops/common-server/docker-compose-agent.yml up -d
```

#### 部署与回滚

```bash
cd ~/service_brain_agent

# 部署：<env> <image_tag>（tag 取自 Jenkins 构建输出）
bash devops/scripts/manual/deploy.sh prod 20261030130003

# 回滚：拿历史 tag 重放（容器会被重建，数据卷不动）
bash devops/scripts/manual/rollback.sh prod 20261029110000

# 镜像仓库不是默认地址、或要换端口时，用环境变量覆盖（见第 7 节变量表）
DOCKER_REPO_URL=registry.example.com DOCKER_REPO_NAMESPACE=team APP_PORT=8080 \
    bash devops/scripts/manual/deploy.sh prod 20261030130003

# 无人值守（远程 ssh 一把梭）时跳过 y/N 确认
yes | bash devops/scripts/manual/deploy.sh prod 20261030130003
```

脚本依次执行：创建共享网络 `service-brain-agent`（缺失时）→ 创建 `devops/data/logs` → `compose pull` → `up -d --remove-orphans`
→ 轮询 `http://127.0.0.1:<APP_PORT>/healthz`（最多 60s，失败打印最近 50 行日志）。

验收：

```bash
docker ps --filter name=service-brain-agent
curl -fsS http://127.0.0.1:8002/healthz
curl -sI http://127.0.0.1:8002/ui | head -1     # 应返回 200
```

#### 停止与重启

停止前先明确「停到哪一层」：只停应用、还是应用 + 同机中间件。两者都不动数据。

```bash
cd ~/service_brain_agent

# 1) 只停应用（保留容器，后续可直接 start）
docker stop service-brain-agent

# 等价 compose 写法；compose 解析清单时要求 APP_IMAGE/ENVIRONMENT 有值，
# 停止不拉镜像，APP_IMAGE 随便填一个占位即可
APP_IMAGE=unused ENVIRONMENT=prod \
    docker compose -p service-brain-agent -f devops/brain-agent-server/docker-compose.yml stop

# 2) 停应用并删除容器（外部网络 service-brain-agent 不会被删除；日志目录保留）
APP_IMAGE=unused ENVIRONMENT=prod \
    docker compose -p service-brain-agent -f devops/brain-agent-server/docker-compose.yml down

# 3) 连中间件一起停（先停应用，再倒序停中间件）
docker stop service-brain-agent
for f in agent minio postgres; do
    docker compose -f devops/common-server/docker-compose-$f.yml down
done
```

重启：

```bash
# 仅重启被 stop 的应用容器（镜像与配置不变）
docker start service-brain-agent

# 或按原有镜像与配置重新拉起（不重新 pull）
APP_IMAGE=<原有镜像全名:tag> ENVIRONMENT=prod \
    docker compose -p service-brain-agent -f devops/brain-agent-server/docker-compose.yml up -d

# 中间件按「postgres → minio → agent」顺序先起，再执行上面的应用启动
for f in postgres minio agent; do
    docker compose -f devops/common-server/docker-compose-$f.yml up -d
done

# 需要换版本时不要手工 up，直接走部署脚本
bash devops/scripts/manual/deploy.sh prod <image_tag>
```

注意：不要使用 `down -v`。应用本身无命名卷，但中间件 compose 的命名卷/数据目录会被一并清理；
回滚只重建容器，数据必须保留。

#### 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 容器已起但报「健康检查未通过」 | 宿主机没装 `curl`（就绪探测用它） | `apt install -y curl` 后重跑 |
| `docker: 'compose' is not a docker command` | 只装了 compose v1 | 安装 docker compose v2 插件 |
| `缺少应用配置文件` | 没放 `backend/env/.env.<env>`，或文件名与 `ENV` 不一致 | 按上面目录约定放置 |
| `$'\r': command not found` | 从 Windows 上传带了 CRLF | 执行第 2 步的 `sed` |
| `permission denied … docker.sock` | 当前用户不在 docker 组 | `sudo usermod -aG docker $USER` 后重新登录 |
| 四头签名报时间戳越界 | 宿主机时钟漂移超出 ±5 分钟窗口 | 开启 chrony / ntpd 时间同步 |
| 模型容器反复重启 | 无外网，`ollama-init` / `rerank-init` 拉不到模型 | 预置 `common-server/volumes/{ollama,rerank/models}` 后再启动 |
| `no such host service-brain-postgres` | 中间件未起，或与应用不在同一网络 | 先起中间件，确认 `docker network inspect service-brain-agent` 内两个容器都在 |
| `stop/down` 时报 `APP_IMAGE 未设置` | compose 解析清单也需要插值变量，即使不拉镜像 | 按「停止与重启」补 `APP_IMAGE=unused ENVIRONMENT=prod`，或直接 `docker stop service-brain-agent` |

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
| `APP_PORT`              | `deploy-defaults.sh`            | `8002`                                                     | 宿主机暴露端口（容器内固定 8002）       |
| `PYTHON_IMAGE`          | `deploy-defaults.sh`            | `astral/uv:python3.12-bookworm-slim`                       | 运行时基础镜像 / 门禁回退容器           |
| `NODE_IMAGE`            | `deploy-defaults.sh`            | `node:22-alpine`                                           | 前端构建基础镜像                        |
| `NPM_REGISTRY`          | `deploy-defaults.sh`            | `https://registry.npmmirror.com`                           | 前端依赖源                              |
| `PYPI_INDEX_URL`        | `deploy-defaults.sh`            | 空（用 `uv.lock` 固化源）                                   | 可选：覆盖 Python 依赖源                |
| `DOCKER_REPO_USERNAME` / `DOCKER_REPO_PASSWORD` | 执行机环境变量 | 无                                                         | 可选；两者须同时提供，仅本次登录用       |

> 容器名、compose 项目名、共享网络名不设变量：三者都固定为 `service-brain-agent`，写在
> `devops/brain-agent-server/docker-compose.yml` 里。一台部署机只跑一个应用容器，改容器名/网络名
> 需直接改编排文件，也不影响 `deploy.sh` 的调用方式（`deploy.sh <IMAGE>`）。

---

## 8. 落地步骤（Checklist）

1. **入库锁文件**：确认 `backend/uv.lock` 已提交（本仓库已从 `.gitignore` 移除该忽略项）；`frontend/package-lock.json` 可选。
2. **配置镜像仓库**：核对 `deploy-defaults.sh` 中 `DOCKER_REPO_URL` / `DOCKER_REPO_NAMESPACE`，或导出同名环境变量覆盖。
3. **准备部署机**：安装 docker + compose 插件；执行机一次性 `docker login <DOCKER_REPO_URL>`。
4. **准备环境文件**：在部署机放置 `backend/env/.env.<env>`（参考 `backend/env/.env.dev`，改 `DATABASE_*`、`LOGGER_LEVEL`、`DEBUG=false`、`RELOAD=false`）；生产口令不要提交入库。
5. **（可选）自建数据库与模型服务**：`devops/common-server` 拉起 PostgreSQL 17 + pgvector、MinIO（S3 对象存储）、Ollama（bge-m3 向量化）与 llama.cpp（bge-reranker-v2-m3 重排序），把 `DATABASE_HOST` / `S3_ENDPOINT` 改为容器名即可；模型与对象存储地址见 6.5 / 6.6。
6. **注册 Runner**：GitLab Runner 打 tag `service-brain-agent`（或改 CI 文件中的 `RUNNER_TAG`）；Jenkins 侧配置节点具备 docker CLI。
7. **启用流水线**：在 `.gitlab-ci.yml` 中取消 `workflow.rules` 注释；或直接用 Jenkins `Build Now` 手动发布。
8. **首轮试运行**：dev 验证 `/ui` 与 `/healthz` → test / uat / prod 逐级手动发布 → 演练一次回滚。

---

## 9. 已知限制与后续扩展

| 限制                                   | 影响                                                     | 后续做法                                                       |
| -------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------- |
| 仅 compose、单副本                     | 更新期间容器重启，服务短暂中断（数秒）                   | 需要零中断或集群化时，在 `deploy.sh` 增加 `DEPLOY_MODE=k8s` 分支 |
| 无数据库迁移链                         | 表结构变更无版本管理，回滚不覆盖 DDL                     | 表结构由启动期各域 bootstrap 幂等建表（仅补缺表）；如需版本管理再引入迁移工具 |
| 前端 lock 未入库                       | 同一 commit 两次构建可能得到不同前端依赖版本             | 提交 `package-lock.json` 并改 `npm ci`                          |
| 健康检查不探 DB                        | `/healthz` 只反映进程存活，数据库不可用时仍报健康        | 需要就绪探针时，新增 `/readyz` 校验 `get_db_version()`          |
| 单机 bind 挂载日志                     | 日志随宿主目录增长，需宿主侧轮转                         | 已启用 loguru 按日轮转（保留 30 天），必要时接入集中日志采集     |
| 向量维度不可原地变更                   | 改 `VECTOR_DIM` 后旧表维度不符，启动时告警、写入报错      | 新建表/库并重新灌入向量；或引入独立向量表版本号                |
| `CREATE EXTENSION` 需超级用户           | 托管数据库的普通应用账号无权建扩展                       | DBA 在实例初始化时执行一次 `CREATE EXTENSION vector`，或关闭 `VECTOR_AUTO_CREATE` |
| 向量索引构建会锁表                      | 大表上 `CREATE INDEX` 期间写入受影响                     | 数据量大时改为人工维护（`VECTOR_AUTO_CREATE=false`），按需 `CREATE INDEX CONCURRENTLY` |
