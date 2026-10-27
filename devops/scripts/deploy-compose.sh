#!/bin/bash
set -e

# ==============================================================================
# compose 形态部署（只起应用容器；PostgreSQL/pgvector 不随本脚本编排，可由
# devops/common-server 自托管或由环境提供，见 devops/README.md）
#
#   用法: deploy-compose.sh <IMAGE> <APP_NAME>
#   依赖变量：APP_PORT / APP_NETWORK / ENVIRONMENT / APP_ENV_FILE
#
#   应用配置不做任何注入渲染：env/.env.${ENVIRONMENT} 由 app.yml bind 挂载到
#   容器 /home/env/.env.${ENVIRONMENT}（只读），应用（pydantic-settings）自行
#   加载；容器仅覆盖 ENVIRONMENT 一个环境变量（见 app.yml）。
#
#   使用外部共享网络 APP_NETWORK（默认 service-brain-agent）与中间件容器互通：
#   应用按容器名（如 service-brain-postgres:5432）直连数据库。compose 中该网络
#   声明为 external，不会自动创建、也不会在 down 时被删除，这里负责在缺失时创建。
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/devops/compose/app.yml"
DATA_DIR="${PROJECT_ROOT}/devops/data"

IMAGE="$1"
APP_NAME="$2"

if [ -z "${IMAGE}" ] || [ -z "${APP_NAME}" ]; then
    echo "用法: $0 <IMAGE> <APP_NAME>" >&2
    exit 1
fi

: "${APP_PORT:?请配置 APP_PORT（宿主机暴露端口，如 8002）}"
: "${APP_NETWORK:?请配置 APP_NETWORK（与中间件共用的 docker 网络名）}"
: "${ENVIRONMENT:?请设置 ENVIRONMENT（目标环境名 dev|test|uat|prod）}"
: "${APP_ENV_FILE:?请设置 APP_ENV_FILE（env/.env.<环境> 的绝对路径）}"

[ -f "${COMPOSE_FILE}" ] || { echo "错误: 缺少 ${COMPOSE_FILE}" >&2; exit 1; }
[ -f "${APP_ENV_FILE}" ] || { echo "错误: 缺少应用配置文件 ${APP_ENV_FILE}" >&2; exit 1; }

# 日志宿主目录（devops/data 已 gitignore）
mkdir -p "${DATA_DIR}/logs"

if ! docker network inspect "${APP_NETWORK}" >/dev/null 2>&1; then
    echo "创建共享网络: ${APP_NETWORK}"
    docker network create "${APP_NETWORK}" >/dev/null
fi

export APP_IMAGE="${IMAGE}"
export APP_NAME="${APP_NAME}"
export APP_PORT APP_NETWORK ENVIRONMENT

cd "${PROJECT_ROOT}"

echo "=========================================="
echo "compose 形态部署"
echo "项目路径: ${PROJECT_ROOT}"
echo "镜像: ${APP_IMAGE}"
echo "容器名: ${APP_NAME}"
echo "配置文件: env/.env.${ENVIRONMENT}（挂载进容器）"
echo "宿主机端口: ${APP_PORT}（容器内固定 8002）"
echo "共享网络: ${APP_NETWORK}"
echo "=========================================="

docker compose -p "${APP_NAME}" -f "${COMPOSE_FILE}" pull
docker compose -p "${APP_NAME}" -f "${COMPOSE_FILE}" up -d --remove-orphans

echo "等待服务就绪（最多 60s）..."
HEALTH_URL="http://127.0.0.1:${APP_PORT}/healthz"
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "${HEALTH_URL}"; then
        echo "健康检查通过: ${HEALTH_URL}"
        docker compose -p "${APP_NAME}" -f "${COMPOSE_FILE}" ps
        exit 0
    fi
    sleep 2
done

echo "错误: 健康检查未通过（${HEALTH_URL}），最近日志：" >&2
docker compose -p "${APP_NAME}" -f "${COMPOSE_FILE}" logs --tail=50 >&2
exit 1
