# 部署与流水线说明（service_brain_agent）

| 项目     | 说明                                                                                       |
| -------- | ------------------------------------------------------------------------------------------ |
| 适用仓库 | `service_brain_agent`（前后端一体化：Vue 3 构建产物由 FastAPI 在 `/ui` 下同源托管）        |
| 文档定位 | 本仓库的 CI/CD 与 devops 落地说明：流水线双入口、单镜像构建、compose 部署、配置与口令契约  |
| 运行形态 | **docker compose 单机部署**（不涉及 Kubernetes）；一个应用容器承载前端静态资源与后端 API   |
| 基线参考 | `fastapi_admin_backend`（后端）与 `fastapi_admin_web`（前端）的 CI + devops 实践，流程骨架沿用、按单体工程简化 |

> **配置唯一来源：应用自身的多环境文件 `env/.env.<env>`**。`.gitlab-ci.yml` / `Jenkinsfile` 不配置任何环境变量（含口令），唯一职责是把目标环境名传给一键脚本 `devops/scripts/00-deploy-all.sh --env=<dev|test|uat|prod>`。应用运行配置不存在任何部署侧副本或渲染产物——`env/.env.<env>` 在部署时**原样 bind 挂载**进容器 `/home/env/.env.<env>`，由应用内 `pydantic-settings` 自行加载。部署侧仅保留少量非口令默认值（`devops/scripts/deploy-defaults.sh`，入库、可被同名环境变量覆盖）；镜像仓库口令依赖执行机一次性 `docker login` 预认证（或临时导出 `DOCKER_REPO_USERNAME` / `DOCKER_REPO_PASSWORD`）。

---

## 1. 方案总览

### 1.1 三段式流水线

```
build（门禁校验） → package（构建并推送镜像） → deploy（docker compose 发布）
```

三段在**同一个 Job / 同一脚本内串接**（`devops/scripts/00-deploy-all.sh --env=<环境名>`），镜像 tag 通过 `image_tag.txt` 在脚本间传递，不跨 Job 传 artifact（规避 GitLab 产物大小限制）。

| 阶段    | 实现                                                                                                  |
| ------- | ----------------------------------------------------------------------------------------------------- |
| build   | `check_app.py`：`app/**` 与 `main.py` 语法可解析；`pyproject.toml` 直接依赖全部出现在 `uv.lock`；`PYTHON_IMAGE` 与 `.python-version` 版本一致；前端工程完整（`package.json` 含 `build`、`vue`/`vite` 依赖、`index.html`、`vite.config.js`、`static/`）；`env/.env.*` 的 `DATABASE_TYPE` 合法且 postgres 下 `VECTOR_DIM` 为正整数 |
| package | `docker build -f devops/config/Dockerfile`：node 阶段构建前端 → uv 阶段装依赖并合并产物；推送时间戳 tag（`yyyyMMddHHmmss`） |
| deploy  | `deploy-compose.sh`：确保共享网络存在 → `pull` → `up -d` → HTTP 健康探测（`/healthz`）                 |

### 1.2 为什么只需要一个容器

本工程前端（`frontend/`）在构建期把产物写入后端 `static/`（`vite.config.js` 的 `build.outDir = ../static`），运行期由 `app.frontend("/ui", ...)` 托管。因此：

- **无 nginx 容器、无跨容器网络调用**：页面与接口同源，`/ui` 与 `/api*` 由同一进程提供，不存在跨域与反向代理配置；
- 前后端版本天然对齐：一次构建出一个镜像，不存在「前端已发、后端未发」的错配；
- 环境差异只由挂载的 `env/.env.<env>` 决定，镜像本身与环境无关（同一镜像可用于四个环境）。

> 如后续引入独立网关（TLS 终结、多域名），只需在网关层反代到 `http://<部署机IP>:<APP_PORT>`，无需改动本仓库脚本。

### 1.3 环境矩阵

| 环境 | 配置文件             | 建议发布方式                                                          | 说明                                     |
| ---- | -------------------- | --------------------------------------------------------------------- | ---------------------------------------- |
| dev  | `env/.env.dev`       | CI 自动（push `develop`）或 Jenkins / Run pipeline 传 `ENV_NAME=dev`  | 开发联调机                               |
| test | `env/.env.test`      | CI（Run pipeline 传 `ENV_NAME=test`）或部署机手动 `manual/deploy.sh test [TAG]` | 测试环境，独立数据库名            |
| uat  | `env/.env.uat`       | 部署机手动 `manual/deploy.sh uat [TAG]`（交互确认）                   | 预发布，验证生产配置                     |
| prod | `env/.env.prod`      | 部署机手动 `manual/deploy.sh prod [TAG]`（交互确认）                  | 生产，口令只在部署机维护                 |

- 发布到哪个环境由 `ENV_NAME` 决定；**发布到哪台机器由 Runner / Agent 所在节点决定**；
- 目标机器上必须有 `env/.env.<env>`（缺文件脚本会报错并提示参考 `env/.env.dev`）；
- 全流程串行（GitLab `resource_group` / Jenkins `disableConcurrentBuilds`），避免并发覆盖。

### 1.4 与参考基座项目的差异

| 维度       | 参考实现（backend + web 双仓库）                       | 本仓库落地                                                        |
| ---------- | ------------------------------------------------------ | ----------------------------------------------------------------- |
| 服务个数   | 后端容器 + 前端 nginx 容器，靠共享 docker 网络互通      | **单容器**：前端静态资源随镜像构建、由 FastAPI 托管                |
| 镜像构建   | 后端镜像（uv）、前端镜像（pnpm + nginx）各一个          | 一个 Dockerfile 两阶段：node 构建前端 → uv 运行时                 |
| 部署形态   | compose（dev/sit）+ Kubernetes（uat/prod）双形态        | **仅 compose**，脚本内预留形态分派点                              |
| 环境命名   | dev / sit / uat / prod                                  | **dev / test / uat / prod**（与本仓库 `env/` 文件一致）           |
| 配置注入   | `backend/env/.env.<env>` 挂载进容器                     | 同左：`env/.env.<env>` 挂载到 `/home/env/`                        |
| 容器端口   | 容器内固定 8001，宿主机端口由 `BACKEND_PORT` 控制       | 容器内固定 **8002**，宿主机端口由 `APP_PORT` 控制（与 `main.py` 默认一致） |
| 启动方式   | 入口脚本 `python main.py run --env=<env>`               | 入口脚本 `python -m uvicorn app:create_app --factory`（关掉热重载） |
| 中间件     | `devops/common-server`（MySQL + Redis + Milvus）        | `devops/common-server`（**PostgreSQL 17 + pgvector**，可选用）      |
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
└── devops/
    ├── README.md                           # 本文件
    ├── compose/
    │   ├── app.yml                         # 应用服务编排（配置 bind 挂载 + 健康检查 + 外部共享网络）
    │   └── .env.example                    # compose 手工执行变量样例（真实 .env 不入库）
    ├── common-server/
    │   ├── docker-compose.yml              # 可选：自建 PostgreSQL 17 + pgvector（与应用同外部网络）
    │   ├── initdb/00-init.sql              # 数据目录首次初始化：CREATE EXTENSION vector
    │   └── .env.example                    # 中间件初始化口令/端口插值样例（真实 .env 不入库）
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
| 应用运行配置       | `env/.env.<env>`                | 是（模板） | 部署时 bind 挂载进容器 `/home/env/.env.<env>`，**部署脚本不解析、不渲染**；非开发环境的口令按部署机维护，不要提交实值 |
| 部署侧非口令默认值 | `devops/scripts/deploy-defaults.sh` | 是   | 镜像仓库地址、镜像名、端口、网络名、基础镜像等；执行机可用同名环境变量覆盖 |
| compose 插值变量   | `devops/compose/.env`、`devops/common-server/.env` | 否（gitignore） | 仅手工执行 compose 时需要；CI 由脚本导出变量 |
| 镜像仓库口令       | 执行机 `~/.docker/config.json` 或 CI 变量 `DOCKER_REPO_USERNAME` / `DOCKER_REPO_PASSWORD` | **否** | 严禁写入仓库任何文件；仅 `package.sh` 推送镜像时使用 |
| 运行数据           | `devops/data/logs`、`devops/common-server/volumes/postgres` | 否（gitignore） | 容器持久化目录，由脚本或 compose 自动创建 |

容器内只有 **一个**环境变量覆盖项：`ENVIRONMENT`（取值 `dev|test|uat|prod`）。应用 `app/config/setting.py` 读取该变量后加载 `/home/env/.env.<ENVIRONMENT>`，因此 `env/.env` 里的 `ENVIRONMENT=dev` 不会影响容器行为。

> `env/.env.<env>` 中的 `HOST` / `PORT` / `RELOAD` 只对**宿主机直跑**（`python main.py`）生效：容器内由入口脚本固定监听 `0.0.0.0:8002` 并关闭热重载（`main.py` 面向本地开发，`reload=True` 不适合容器）。**改容器内端口需同步改** `devops/config/entrypoint/00-select-env.sh`、`devops/compose/app.yml` 的 healthcheck 与 Dockerfile 的 `EXPOSE`。

---

## 4. 镜像构建

### 4.1 构建门禁（`build.sh` → `check_app.py`）

任一校验失败即终止发布，避免把明显有问题的代码送进镜像：

1. `app/**/*.py` 与 `main.py` 语法可解析（`ast.parse`）；
2. `uv.lock` 存在，且 `pyproject.toml` 声明的直接依赖全部出现在锁文件（`uv sync --frozen` 才不会在镜像内失败）；
3. `PYTHON_IMAGE` 与 `.python-version`（3.12）主次版本一致；
4. 前端工程完整：`frontend/package.json` 可解析、含 `build` 脚本、`vue`（dependencies）与 `vite`（devDependencies）、无 `file:` / `link:` 本地路径依赖、`index.html` / `vite.config.js` / `static/` 存在。

> **前置要求：`uv.lock` 必须入库**（本仓库 `.gitignore` 已移除该忽略项）。前端 lock 文件当前未入库，故镜像内使用 `npm install`；如需可复现的前端依赖，提交 `frontend/package-lock.json` 并把 Dockerfile 中的 `npm install` 改为 `npm ci`。

### 4.2 镜像结构（`devops/config/Dockerfile`）

| 阶段 | 基础镜像                        | 作用                                                                 |
| ---- | ------------------------------- | -------------------------------------------------------------------- |
| web  | `node:22-alpine`                | `npm install` + `npm run build`，产物落在 `/build/static`             |
| 运行 | `astral/uv:python3.12-bookworm-slim` | `uv sync --frozen --no-dev` 装依赖 → 复制 `app/`、`main.py`、`static/` → 合并前端产物 → 入口脚本 |

- `WORKDIR=/home`：`app/config/path_conf.py` 的 `BASE_DIR` 由代码位置推导为 `/home`，日志写 `/home/logs`（compose 挂载 `devops/data/logs`）、静态资源读 `/home/static`；
- 镜像**不含 `env/`**：`Dockerfile.dockerignore` 排除，`package.sh` 构建后再断言 `/home/env` 为空、且无前端工程目录；
- 依赖源：`uv.lock` 已固化镜像源地址；内网构建可传 `--build-arg PYPI_INDEX_URL=<私有源>` 与 `--build-arg NPM_REGISTRY=<私有 npm 源>`（默认 `https://registry.npmmirror.com`）。

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
  GIT_CLEAN_FLAGS: "-e env/ -e devops/data/"

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

- **默认关闭**（`when: never`）。确认 Runner 已注册、部署机已备好 `env/.env.<env>` 与 `docker login` 后，取消上方两条 `rules` 注释即可启用（push `develop` 自动发布 dev；`Run pipeline` 手动指定环境）；
- `GIT_CLEAN_FLAGS` 必须保留：Runner 默认 `git clean -ffdx` 会删掉 gitignored 的 `env/` 实值文件与 `devops/data/` 运行数据。

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

1. `deploy-defaults.sh` → `init_deploy_env <env>`：校验环境名、导出默认值、检查 `env/.env.<env>` 存在；
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

### 6.3 中间件：自建 PostgreSQL + pgvector（可选）

```bash
# 一次性拉起（长期运行，不随应用发布重启）
cp devops/common-server/.env.example devops/common-server/.env   # 填 POSTGRES_PASSWORD 等
docker compose -f devops/common-server/docker-compose.yml up -d
```

镜像 `pgvector/pgvector:pg17` 自带 pgvector 扩展（实测 0.8.7）。`initdb/00-init.sql` 在数据目录**首次**初始化时执行
`CREATE EXTENSION IF NOT EXISTS vector`；数据卷已存在时会跳过 initdb，此时由应用启动阶段按 `VECTOR_AUTO_CREATE`
幂等补建（实现在 `app/core/vector_store.py`）。两条路径均幂等，重复执行无副作用。

自建后把 `env/.env.<env>` 指向容器名即可（同一 `APP_NETWORK`）：

```ini
DATABASE_TYPE=postgres
DATABASE_HOST=service-brain-postgres
DATABASE_PORT=5432
DATABASE_NAME=service_brain_agent           # 须与 devops/common-server/.env 的 POSTGRES_DB 一致
DATABASE_USER=postgres
DATABASE_PASSWORD=<与 devops/common-server/.env 的 POSTGRES_PASSWORD 一致>
VECTOR_ENABLE=true
VECTOR_DIM=1024                      # 须与 embedding 模型输出一致（bge-m3=1024）
VECTOR_AUTO_CREATE=true
```

> - 口令两边必须一致，且只存在于部署机（`env/.env.<env>` 与 `devops/common-server/.env`，均 gitignore / 不入库）；
> - 生产建议为应用单独建库账号并收敛权限，但 `CREATE EXTENSION` 需要超级用户：由 DBA 在部署机初始化时执行一次，
> 应用账号只需库的 `USAGE` 与表读写权限；
> - 同一台机器跑多个环境（如 test / prod）时，`POSTGRES_DB` 与 `DATABASE_NAME` 需逐环境对齐，或各起一套编排（改容器名与 `POSTGRES_PORT`）；
> - 应用侧 `DATABASE_TYPE=postgres` 时，连接串不再拼接 `charset`（asyncpg 遇到未知 query 参数会在建连阶段直接报错）。

### 6.4 向量存储（pgvector）

切块表 `kb_chunk`：`space_id` / `doc_id` / `chunk_index` 唯一约束（重复向量化幂等覆盖）、`content` 原文、
`embedding vector(<VECTOR_DIM>)`、`meta jsonb`，索引为 HNSW + `vector_cosine_ops`（m=16, ef_construction=64）。

| 接口 | 说明 |
| ---- | ---- |
| `GET /system/vector/status` | 自检：是否启用、维度、表名、索引名、切块总量、不可用原因 |
| `POST /system/vector/chunks/upsert` | 批量写入切块（含向量），按 (space_id, doc_id, chunk_index) 幂等覆盖，建议 ≤32 条/批 |
| `POST /system/vector/chunks/search` | 余弦相似度召回，支持 `space_id` / `doc_id` 过滤、`top_k`、`min_score` |
| `DELETE /system/vector/chunks` | 按 `doc_id`（可选 `space_id`）删除整篇文档的切块 |

- **分数口径**：`score = 1 - cosine_distance`，越大越相似；阈值需按实际 embedding 模型重标，不要跨模型复用；
- **embedding 不在本工程生成**：接口接收调用方算好的向量，只做维度校验与存取，不绑定模型供应商；
- **维度不可原地变更**：`kb_chunk` 的向量列类型在进程导入期定型，`VECTOR_DIM` 改动后启动时会检测到表维度不一致并报错，
  需新建表/库并重新灌入（与 `devops/README` 第 9 节限制一致）；
- **接口不在审计范围内**：向量接口未挂 `OperationLogRoute`，避免上千维浮点数组写入审计表；
- **排障入口**：`GET /system/vector/status` 返回 `chunk_total` 与 `message`，可快速区分「扩展未建」「表不存在」「配置未启用」。

### 6.5 运维常用命令

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
```

---

## 7. 变量清单

| 变量                    | 来源                            | 默认值                                                     | 说明                                   |
| ----------------------- | ------------------------------- | ---------------------------------------------------------- | -------------------------------------- |
| `ENV_NAME` / `ENVIRONMENT` | CI 变量 / 脚本参数           | `dev`                                                      | 目标环境，决定挂载哪个 `env/.env.<env>` |
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

1. **入库锁文件**：确认 `uv.lock` 已提交（本仓库已从 `.gitignore` 移除该忽略项）；`frontend/package-lock.json` 可选。
2. **配置镜像仓库**：核对 `deploy-defaults.sh` 中 `DOCKER_REPO_URL` / `DOCKER_REPO_NAMESPACE`，或导出同名环境变量覆盖。
3. **准备部署机**：安装 docker + compose 插件；执行机一次性 `docker login <DOCKER_REPO_URL>`。
4. **准备环境文件**：在部署机放置 `env/.env.<env>`（参考 `env/.env.dev`，改 `DATABASE_*`、`LOGGER_LEVEL`、`DEBUG=false`、`RELOAD=false`）；生产口令不要提交入库。
5. **（可选）自建数据库**：`devops/common-server` 拉起 PostgreSQL 17 + pgvector，并把 `DATABASE_HOST` 改为容器名；向量能力自检看 `GET /system/vector/status`。
6. **注册 Runner**：GitLab Runner 打 tag `service-brain-agent`（或改 CI 文件中的 `RUNNER_TAG`）；Jenkins 侧配置节点具备 docker CLI。
7. **启用流水线**：在 `.gitlab-ci.yml` 中取消 `workflow.rules` 注释；或直接用 Jenkins `Build Now` 手动发布。
8. **首轮试运行**：dev 验证 `/ui` 与 `/healthz` → test / uat / prod 逐级手动发布 → 演练一次回滚。

---

## 9. 已知限制与后续扩展

| 限制                                   | 影响                                                     | 后续做法                                                       |
| -------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------- |
| 仅 compose、单副本                     | 更新期间容器重启，服务短暂中断（数秒）                   | 需要零中断或集群化时，在 `deploy.sh` 增加 `DEPLOY_MODE=k8s` 分支 |
| 无数据库迁移链                         | 表结构变更无版本管理，回滚不覆盖 DDL                     | 接入 Alembic（`app/alembic/versions` 目录已就位）并在启动前执行 `upgrade head` |
| 前端 lock 未入库                       | 同一 commit 两次构建可能得到不同前端依赖版本             | 提交 `package-lock.json` 并改 `npm ci`                          |
| 健康检查不探 DB                        | `/healthz` 只反映进程存活，数据库不可用时仍报健康        | 需要就绪探针时，新增 `/readyz` 校验 `get_db_version()`          |
| 单机 bind 挂载日志                     | 日志随宿主目录增长，需宿主侧轮转                         | 已启用 loguru 按日轮转（保留 30 天），必要时接入集中日志采集     |
| 向量维度不可原地变更                   | 改 `VECTOR_DIM` 后旧表维度不符，启动时告警、写入报错      | 新建表/库并重新灌入向量；或引入独立向量表版本号                |
| `CREATE EXTENSION` 需超级用户           | 托管数据库的普通应用账号无权建扩展                       | DBA 在实例初始化时执行一次 `CREATE EXTENSION vector`，或关闭 `VECTOR_AUTO_CREATE` |
| 向量索引构建会锁表                      | 大表上 `CREATE INDEX` 期间写入受影响                     | 数据量大时改为人工维护（`VECTOR_AUTO_CREATE=false`），按需 `CREATE INDEX CONCURRENTLY` |
