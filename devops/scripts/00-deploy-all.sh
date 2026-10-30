#!/bin/bash
set -eo pipefail

# ==============================================================================
# CI 一键入口：build -> package -> deploy，目标环境由 --env 参数指定
#
#   用法: 00-deploy-all.sh --env=<dev|test|uat|prod>
#   示例: 00-deploy-all.sh --env=dev
#
#   流水线（Jenkinsfile / .gitlab-ci.yml）不配置任何环境变量，只传环境名。
#   应用运行配置的唯一来源是 backend/env/.env.<env>：部署时挂载进容器、由应用自行加载，
#   脚本不做任何渲染。部署侧变量（镜像名、端口、网络等）取
#   devops/scripts/deploy-defaults.sh 的默认值（执行机可用同名环境变量覆盖）。
#
#   镜像仓库推送凭证不入库：执行机一次性 docker login 预认证，或临时导出
#   DOCKER_REPO_USERNAME / DOCKER_REPO_PASSWORD（见 package.sh）。
#
#   IMAGE_TAG 可由流水线变量临时指定（缺省取时间戳 yyyyMMddHHmmss）。
# ==============================================================================

ENV_NAME=""
for arg in "$@"; do
    case "${arg}" in
        --env=*)
            ENV_NAME="${arg#--env=}"
            ;;
        *)
            echo "错误: 无法识别的参数 ${arg}" >&2
            echo "用法: $0 --env=<dev|test|uat|prod>" >&2
            exit 1
            ;;
    esac
done

if [ -z "${ENV_NAME}" ]; then
    echo "错误: 缺少环境参数" >&2
    echo "用法: $0 --env=<dev|test|uat|prod>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${SCRIPT_DIR}/deploy-defaults.sh"
init_deploy_env "${ENV_NAME}"

IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
export IMAGE_TAG

cd "${PROJECT_ROOT}"

echo "============================================="
echo "      service_brain_agent 一键构建+打包+部署"
echo "============================================="
echo "项目根目录：${PROJECT_ROOT}"
echo "目标环境：${ENV_NAME}"
echo "配置文件：backend/env/.env.${ENV_NAME}（挂载进容器）"
echo "镜像仓库：${DOCKER_REPO_URL}/${DOCKER_REPO_NAMESPACE}"
echo "镜像 TAG：${IMAGE_TAG}"
echo "宿主机端口：${APP_PORT}（容器内固定 8002）"
echo "============================================="

git config --global --add safe.directory "${PROJECT_ROOT}" || true

echo -e "\n[1/3] 代码校验（语法 + 依赖一致性 + 前端工程完整性）..."
chmod +x "${SCRIPT_DIR}/build.sh"
"${SCRIPT_DIR}/build.sh"

echo -e "\n[2/3] 构建并推送镜像..."
chmod +x "${SCRIPT_DIR}/package.sh"
"${SCRIPT_DIR}/package.sh"

echo -e "\n[3/3] 部署（docker compose）..."
APP_IMAGE_TAG="$(cat "${PROJECT_ROOT}/image_tag.txt")"
chmod +x "${SCRIPT_DIR}/deploy.sh" "${SCRIPT_DIR}/deploy-compose.sh"
"${SCRIPT_DIR}/deploy.sh" "${IMAGE_NAME_APP}:${APP_IMAGE_TAG}"

echo -e "\n service_brain_agent 部署完成（env=${ENV_NAME}）"
echo " 前端页面：http://<部署机IP>:${APP_PORT}/ui"
echo " 健康检查：http://<部署机IP>:${APP_PORT}/healthz"
