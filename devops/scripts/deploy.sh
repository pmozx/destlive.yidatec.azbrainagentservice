#!/bin/bash
set -e

# ==============================================================================
# 部署入口（本方案只有 docker compose 一种形态）
#
#   用法: deploy.sh <IMAGE>
#     IMAGE     完整镜像名:tag
#
#   容器名 / compose 项目名 / 共享网络名固定为 service-brain-agent（见
#   devops/brain-agent-server/docker-compose.yml），不用传参，也不需要环境变量。
#
#   应用运行配置不做任何渲染：backend/env/.env.${ENVIRONMENT} 由 compose bind 挂载进
#   容器，应用自行加载。
#
#   依赖变量：ENVIRONMENT（目标环境名）、APP_PORT、APP_ENV_FILE
#
#   后续如需接入 Kubernetes，可在此按 DEPLOY_MODE 分派到 deploy-k8s.sh，
#   当前阶段仅落地 compose，避免维护未被使用的编排文件。
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE="$1"

if [ -z "${IMAGE}" ]; then
    echo "用法: $0 <IMAGE>" >&2
    exit 1
fi

: "${ENVIRONMENT:?请设置 ENVIRONMENT（目标环境名 dev|test|uat|prod）}"
: "${APP_ENV_FILE:?请设置 APP_ENV_FILE（backend/env/.env.<环境> 的绝对路径）}"

if [ ! -f "${APP_ENV_FILE}" ]; then
    echo "错误: 缺少应用配置文件 backend/env/.env.${ENVIRONMENT}" >&2
    echo "      请参考 backend/env/.env.dev 创建并填写" >&2
    exit 1
fi
export APP_ENV_FILE

exec "${SCRIPT_DIR}/deploy-compose.sh" "${IMAGE}"
