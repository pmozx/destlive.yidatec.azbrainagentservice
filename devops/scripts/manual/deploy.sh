#!/bin/bash
set -e

# ==============================================================================
# 手动部署入口（test / uat / prod 等非 CI 环境）
#
#   用法: deploy.sh <ENV> [IMAGE_TAG]
#   示例:
#     deploy.sh prod 20260921120000    # 部署指定 tag
#     deploy.sh uat                    # 未传 tag 时交互输入
#
#   ENV ∈ dev | test | uat | prod，作用：
#     1. 作为 ENVIRONMENT 传给部署脚本，决定挂载 backend/env/.env.<ENV> 进容器；
#     2. 决定镜像名与端口等部署侧默认值（见 deploy-defaults.sh）。
#
#   应用配置唯一来源 backend/env/.env.<ENV>（非开发环境的口令在部署机本地填写，
#   不要提交入库）；部署侧变量取 deploy-defaults.sh 默认值，可用同名环境
#   变量覆盖；镜像仓库凭证不入库（执行机 docker login 预认证或临时导出
#   DOCKER_REPO_USERNAME / DOCKER_REPO_PASSWORD）。
# ==============================================================================

if [ $# -lt 1 ]; then
    echo "用法: $0 <ENV> [IMAGE_TAG]" >&2
    echo "  ENV ∈ dev | test | uat | prod" >&2
    exit 1
fi

ENV_NAME="$1"
IMAGE_TAG="${2:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${SCRIPT_DIR}/../deploy-defaults.sh"
init_deploy_env "${ENV_NAME}"

APP_NAME="${APP_NAME_APP}"

if [ -z "${IMAGE_TAG}" ]; then
    read -rp "请输入要部署的镜像 TAG（CI 产物格式 yyyyMMddHHmmss）: " IMAGE_TAG
fi
: "${IMAGE_TAG:?镜像 TAG 不能为空}"

echo "=========================================="
echo "手动部署 service_brain_agent"
echo "环境: ${ENV_NAME}"
echo "配置文件: backend/env/.env.${ENV_NAME}（挂载进容器）"
echo "镜像: ${IMAGE_NAME_APP}:${IMAGE_TAG}"
echo "应用名称: ${APP_NAME}"
echo "宿主机端口: ${APP_PORT}"
echo "=========================================="

read -rp "确认部署？(y/N) " confirm
case "${confirm}" in
    y|Y|yes|YES) ;;
    *)
        echo "已取消"
        exit 0
        ;;
esac

cd "${PROJECT_ROOT}"
chmod +x "${SCRIPT_DIR}/../deploy.sh" "${SCRIPT_DIR}/../deploy-compose.sh"
"${SCRIPT_DIR}/../deploy.sh" "${IMAGE_NAME_APP}:${IMAGE_TAG}" "${APP_NAME}"
