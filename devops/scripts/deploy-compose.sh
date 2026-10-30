#!/bin/bash
set -e

# ==============================================================================
# compose 形态部署（只起应用容器；PostgreSQL/pgvector 不随本脚本编排，可由
# devops/common-server 自托管或由环境提供，见 devops/README.md）
#
#   用法: deploy-compose.sh <IMAGE>
#   依赖变量：APP_PORT / ENVIRONMENT / APP_ENV_FILE（APP_IMAGE 由参数传入）
#
#   容器名、compose 项目名、共享网络名统一固定为 service-brain-agent，不再从变量读取：
#   一台部署机只跑一个应用容器，不需要为改名/换网络留出变量。
#
#   应用配置不做任何注入渲染：backend/env/.env.${ENVIRONMENT} 由 docker-compose.yml
#   bind 挂载到容器 /home/env/.env.${ENVIRONMENT}（只读），应用（pydantic-settings）
#   自行加载；容器仅覆盖 ENVIRONMENT 一个环境变量（见 devops/brain-agent-server/docker-compose.yml）。
#   编排文件本身不读 .env：下面这些变量直接 export 给 compose 插值，缺失即报错。
#
#   使用外部共享网络 service-brain-agent 与中间件容器互通：应用按容器名
#   （如 service-brain-postgres:5432）直连数据库。compose 中该网络声明为
#   external，不会自动创建、也不会在 down 时被删除，这里负责在缺失时创建。
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/devops/brain-agent-server/docker-compose.yml"
DATA_DIR="${PROJECT_ROOT}/devops/data"

IMAGE="$1"

if [ -z "${IMAGE}" ]; then
    echo "用法: $0 <IMAGE>" >&2
    exit 1
fi

# 容器名 / compose 项目名 / 共享网络名：与 docker-compose.yml 保持一致，不读变量
COMPOSE_PROJECT="service-brain-agent"
NETWORK="service-brain-agent"

: "${APP_PORT:?请配置 APP_PORT（宿主机暴露端口，如 8002）}"
: "${ENVIRONMENT:?请设置 ENVIRONMENT（目标环境名 dev|test|uat|prod）}"
: "${APP_ENV_FILE:?请设置 APP_ENV_FILE（backend/env/.env.<环境> 的绝对路径）}"

[ -f "${COMPOSE_FILE}" ] || { echo "错误: 缺少 ${COMPOSE_FILE}" >&2; exit 1; }
[ -f "${APP_ENV_FILE}" ] || { echo "错误: 缺少应用配置文件 ${APP_ENV_FILE}" >&2; exit 1; }

# 日志宿主目录（devops/data 已 gitignore）
mkdir -p "${DATA_DIR}/logs"

if ! docker network inspect "${NETWORK}" >/dev/null 2>&1; then
    echo "创建共享网络: ${NETWORK}"
    docker network create "${NETWORK}" >/dev/null
fi

export APP_IMAGE="${IMAGE}"
export APP_PORT ENVIRONMENT

cd "${PROJECT_ROOT}"

echo "=========================================="
echo "compose 形态部署"
echo "项目路径: ${PROJECT_ROOT}"
echo "镜像: ${APP_IMAGE}"
echo "容器名: ${COMPOSE_PROJECT}"
echo "配置文件: backend/env/.env.${ENVIRONMENT}（挂载进容器）"
echo "宿主机端口: ${APP_PORT}（容器内固定 8002）"
echo "共享网络: ${NETWORK}"
echo "=========================================="

docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" pull
docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" up -d --remove-orphans

echo "等待服务就绪（最多 60s）..."
HEALTH_URL="http://127.0.0.1:${APP_PORT}/healthz"
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "${HEALTH_URL}"; then
        echo "健康检查通过: ${HEALTH_URL}"
        docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" ps
        exit 0
    fi
    sleep 2
done

echo "错误: 健康检查未通过（${HEALTH_URL}），最近日志：" >&2
docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" logs --tail=50 >&2
exit 1
