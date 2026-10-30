#!/bin/bash
set -e

# ==============================================================================
# 手动回滚入口（test / uat / prod 等非 CI 环境）
#
#   用法: rollback.sh <ENV> [IMAGE_TAG]
#   示例:
#     rollback.sh prod 20260921120000    # 用历史 tag 重新部署
#     rollback.sh uat                    # 交互输入历史 tag
#
#   ENV ∈ dev | test | uat | prod，与 manual/deploy.sh 一致（配置唯一来源
#   backend/env/.env.<ENV>，部署侧变量取 deploy-defaults.sh 默认值）。
#
#   compose 无 Deployment 版本历史，回滚方式为「用历史镜像 tag 重新 up -d」：
#   镜像 tag 为时间戳（不可变），历史 tag 可在镜像仓库列表或部署机
#   `docker images` 中查到。
#
#   注意：镜像回滚不改变数据库结构。若目标版本早于最近一次表结构变更，
#   需人工确认 DDL 是否可回退（本工程当前未接入迁移工具，表结构变更需
#   由发布流程单独评审）。
# ==============================================================================

if [ $# -lt 1 ]; then
    echo "用法: $0 <ENV> [IMAGE_TAG]" >&2
    echo "  ENV ∈ dev | test | uat | prod" >&2
    exit 1
fi

ENV_NAME="$1"
TARGET="${2:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${SCRIPT_DIR}/../deploy-defaults.sh"
init_deploy_env "${ENV_NAME}"

# 容器名 / compose 项目名固定为 service-brain-agent（见 docker-compose.yml）
COMPOSE_PROJECT="service-brain-agent"

echo "=========================================="
echo "手动回滚 service_brain_agent"
echo "环境: ${ENV_NAME}"
echo "容器名: ${COMPOSE_PROJECT}"
echo "=========================================="

if [ -z "${TARGET}" ]; then
    read -rp "请输入要回滚到的历史镜像 TAG: " TARGET
fi
: "${TARGET:?镜像 TAG 不能为空}"

echo ""
echo "当前运行容器："
docker compose -p "${COMPOSE_PROJECT}" -f "${PROJECT_ROOT}/devops/brain-agent-server/docker-compose.yml" ps || true
echo ""

read -rp "确认用 ${IMAGE_NAME_APP}:${TARGET} 重新部署？(y/N) " confirm
case "${confirm}" in
    y|Y|yes|YES) ;;
    *)
        echo "已取消"
        exit 0
        ;;
esac

cd "${PROJECT_ROOT}"
chmod +x "${SCRIPT_DIR}/../deploy.sh" "${SCRIPT_DIR}/../deploy-compose.sh"
"${SCRIPT_DIR}/../deploy.sh" "${IMAGE_NAME_APP}:${TARGET}"

echo ""
echo "回滚完成，当前容器状态："
docker compose -p "${COMPOSE_PROJECT}" -f "${PROJECT_ROOT}/devops/brain-agent-server/docker-compose.yml" ps
