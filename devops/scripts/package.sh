#!/bin/bash
set -e

# ==============================================================================
# 镜像构建与推送（多阶段：node 构建前端 → python 运行时）
#
#   - 依赖变量（默认值见 deploy-defaults.sh，可被执行机环境变量覆盖）：
#               DOCKER_REPO_URL、DOCKER_REPO_NAMESPACE、IMAGE_NAME_APP、
#               PYTHON_IMAGE、NODE_IMAGE、NPM_REGISTRY、PYPI_INDEX_URL
#   - 镜像仓库凭证不入库：执行机一次性 docker login 预认证；或导出
#     DOCKER_REPO_USERNAME / DOCKER_REPO_PASSWORD（两者须同时提供，仅本次登录用）
#   - 构建产物 tag 写入 image_tag.txt，供同 Job 内的部署脚本读取
#   - 镜像内不含任何 .env 文件：由 Dockerfile.dockerignore 排除，并在构建后再次断言
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

: "${DOCKER_REPO_URL:?请配置 DOCKER_REPO_URL（镜像仓库地址）}"
: "${IMAGE_NAME_APP:?请配置 IMAGE_NAME_APP（镜像全名，不含 tag）}"
: "${PYTHON_IMAGE:?请配置 PYTHON_IMAGE（如 astral/uv:python3.12-bookworm-slim）}"
: "${NODE_IMAGE:?请配置 NODE_IMAGE（如 node:22-alpine）}"

IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
DOCKERFILE="devops/config/Dockerfile"
IGNORE_FILE="${DOCKERFILE}.dockerignore"

cd "${PROJECT_ROOT}"

[ -f "${DOCKERFILE}" ] || { echo "错误: 缺少 ${DOCKERFILE}" >&2; exit 1; }
[ -f "${IGNORE_FILE}" ] || { echo "错误: 缺少 ${IGNORE_FILE}（镜像构建上下文排除清单）" >&2; exit 1; }
[ -f "${PROJECT_ROOT}/uv.lock" ] || { echo "错误: 缺少 uv.lock（依赖锁文件必须入库）" >&2; exit 1; }

# <Dockerfile>.dockerignore 是 BuildKit 特性：该文件存在时会「替代」根 .dockerignore，
# 这里显式启用 BuildKit，避免普通 builder 忽略它而把 env/、.venv 等打进镜像
export DOCKER_BUILDKIT=1

echo "=========================================="
echo "构建运行镜像（前端 + 后端单镜像）"
echo "镜像: ${IMAGE_NAME_APP}:${IMAGE_TAG}"
echo "Dockerfile: ${DOCKERFILE}"
echo "Python 基础镜像: ${PYTHON_IMAGE}"
echo "Node 构建镜像: ${NODE_IMAGE}"
echo "=========================================="

BUILD_ARGS=()
if [ -n "${NPM_REGISTRY:-}" ]; then
    BUILD_ARGS+=(--build-arg "NPM_REGISTRY=${NPM_REGISTRY}")
fi
if [ -n "${PYPI_INDEX_URL:-}" ]; then
    BUILD_ARGS+=(--build-arg "PYPI_INDEX_URL=${PYPI_INDEX_URL}")
fi

docker build \
    -f "${DOCKERFILE}" \
    "${BUILD_ARGS[@]}" \
    -t "${IMAGE_NAME_APP}:${IMAGE_TAG}" \
    .

echo "${IMAGE_TAG}" > "${PROJECT_ROOT}/image_tag.txt"

# 断言：镜像内不允许存在环境文件与前端工作区（配置只能由部署期注入）
if docker run --rm --entrypoint sh "${IMAGE_NAME_APP}:${IMAGE_TAG}" \
    -c 'ls -A /home/env 2>/dev/null | grep -q . || ls -d /home/frontend 2>/dev/null'; then
    echo "错误: 镜像内包含 /home/env 下的文件或前端工程目录，配置/源码可能被打包" >&2
    exit 1
fi
echo "校验通过: 镜像内无 env 文件、无前端工程目录"

# 推送凭证：优先环境变量（用户名/口令须同时提供）；否则依赖执行机 docker login 预认证
if [ -n "${DOCKER_REPO_USERNAME:-}" ]; then
    : "${DOCKER_REPO_PASSWORD:?已设置 DOCKER_REPO_USERNAME，须同时提供 DOCKER_REPO_PASSWORD}"
    echo "登录镜像仓库（使用环境变量凭证）..."
    echo "${DOCKER_REPO_PASSWORD}" | docker login "${DOCKER_REPO_URL}" -u "${DOCKER_REPO_USERNAME}" --password-stdin
fi

echo "推送镜像..."
if ! docker push "${IMAGE_NAME_APP}:${IMAGE_TAG}"; then
    echo "错误: 镜像推送失败" >&2
    echo "      请确认执行机已 docker login ${DOCKER_REPO_URL}（一次性预认证），" >&2
    echo "      或导出 DOCKER_REPO_USERNAME / DOCKER_REPO_PASSWORD 后重试" >&2
    exit 1
fi

echo "=========================================="
echo "镜像已推送: ${IMAGE_NAME_APP}:${IMAGE_TAG}"
echo "tag 已写入: ${PROJECT_ROOT}/image_tag.txt"
echo "=========================================="
