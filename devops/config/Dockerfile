# syntax=docker/dockerfile:1
# ==============================================================================
# 服务大脑智能体（service_brain_agent）运行镜像 —— 单镜像多环境（dev / test / uat / prod）
#
#   构建上下文 = 仓库根目录（由 devops/scripts/package.sh 调用）：
#     docker build -f devops/config/Dockerfile \
#       --build-arg NPM_REGISTRY=... --build-arg PYPI_INDEX_URL=... \
#       -t <IMAGE_NAME>:<TAG> .
#
#   本工程为前后端一体化：前端在镜像内构建（node 阶段），产物并入后端
#   backend/static/，由 FastAPI 的 app.frontend("/ui", ...) 同源托管，故运行期
#   只需一个容器。
#
#   镜像不含任何环境配置：backend/env/ 由 Dockerfile.dockerignore 排除，运行期由
#   部署侧把 backend/env/.env.<环境名> 原样挂载到 /home/env/（compose bind mount），
#   应用经 pydantic-settings 自行加载；容器级环境变量只覆盖 ENVIRONMENT 一项。
#
#   构建上下文里的后端代码在 backend/ 下，镜像内仍展平到 /home（main.py、app/、
#   static/ 平级），故 WORKDIR 固定 /home：backend/app/config/path_conf.py 的
#   BASE_DIR 由代码位置推导为 /home，日志目录 /home/logs、静态目录 /home/static
#   均以此为前提，变更 WORKDIR 会导致日志与前端托管路径错位。
# ==============================================================================

# ------------------------------------------------------------------------------
# 阶段一：前端构建（Vue 3 + Vite 7）
#   vite.config.js 的 build.outDir = ../backend/static，产物落在 /build/backend/static
#   （相对 /build/frontend 的上一级），随后整体并入运行镜像的 /home/static
# ------------------------------------------------------------------------------
FROM node:22-alpine AS web

# 国内/内网加速：默认 npmmirror（与 frontend/.npmrc 一致），可用 --build-arg 覆盖
ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /build/frontend

# 先装依赖：仅 package.json 变更时复用该层
# 前端未提交 lock 文件（pnpm-lock/package-lock 均在 .gitignore 中），故用 npm install；
# 若后续提交了 package-lock.json，可将此处改为 npm ci 以获得可复现构建
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund --registry "${NPM_REGISTRY}"

# 源码（node_modules 由 .dockerignore 排除）
COPY frontend/ ./

# 构建前 clean.mjs 只清理 static 下的构建产物，不影响后端演示资源
RUN npm run build

# ------------------------------------------------------------------------------
# 阶段二：Python 运行时
# ------------------------------------------------------------------------------
FROM astral/uv:python3.12-bookworm-slim

# 可选：覆盖依赖索引（uv.lock 已固化镜像源地址，内网构建时可整体指向私有源）
ARG PYPI_INDEX_URL=

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# 依赖装在 /home/.venv，入口脚本的 python / uvicorn 均取自该解释器
ENV PATH="/home/.venv/bin:${PATH}"

WORKDIR /home

# 先装依赖：uv 按 uv.lock 精确还原（--frozen：锁文件与 pyproject.toml 不一致即失败），
# 仅 backend/pyproject.toml / backend/uv.lock 变更时重建该层；--no-install-project 不把
# 本工程自身装进 venv（服务以 /home 为 CWD 直接跑 app 包）
COPY backend/pyproject.toml backend/uv.lock /home/
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "${PYPI_INDEX_URL}" ]; then \
        uv sync --frozen --no-dev --no-install-project --index-url "${PYPI_INDEX_URL}"; \
    else \
        uv sync --frozen --no-dev --no-install-project; \
    fi

# 应用代码与后端静态资源（backend/env、backend/logs、backend/data、.venv、static/assets
# 等由 .dockerignore 排除）；sql/ 是启动期种子数据（一张表一个同名 JSON），必须进镜像，
# 否则容器内 InitializeData 读不到文件、种子静默不导入
COPY backend/app/ /home/app/
COPY backend/sql/ /home/sql/
COPY backend/main.py backend/banner.txt /home/
COPY backend/static/ /home/static/

# 前端构建产物覆盖到 static（index.html / assets / favicon.svg）
COPY --from=web /build/backend/static/ /home/static/

# exec 形式的 ENTRYPOINT 不会展开 ${ENVIRONMENT}，环境选择由入口脚本完成
COPY devops/config/entrypoint/00-select-env.sh /usr/local/bin/00-select-env.sh
RUN chmod +x /usr/local/bin/00-select-env.sh

# 容器内固定监听 8002（与 main.py 默认端口一致）；宿主机暴露端口由 compose 的
# APP_PORT 控制，勿改动此处——healthcheck 与部署脚本的就绪探测均按 8002 探活
EXPOSE 8002

ENTRYPOINT ["/usr/local/bin/00-select-env.sh"]
