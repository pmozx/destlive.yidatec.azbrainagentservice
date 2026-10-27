#!/bin/bash
set -e

# ==============================================================================
# 构建门禁（镜像构建前的静态校验）
#
#   - 依赖变量：PYTHON_IMAGE（由 deploy-defaults.sh 注入，默认 astral/uv:python3.12-bookworm-slim）
#   - 本地有 Python 3.11+（含 tomllib）时直接本地校验，否则回退到 PYTHON_IMAGE 容器内执行
#   - 校验项见 devops/scripts/check_app.py（仅标准库，无第三方依赖）
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

: "${PYTHON_IMAGE:?请配置 PYTHON_IMAGE（如 python:3.12-slim）}"

CHECKER="${SCRIPT_DIR}/check_app.py"

cd "${PROJECT_ROOT}"

echo "=========================================="
echo "开始构建门禁（静态校验）"
echo "项目路径: ${PROJECT_ROOT}"
echo "=========================================="

if command -v python3 >/dev/null 2>&1 && python3 -c "import tomllib" >/dev/null 2>&1; then
    echo "使用本地 python3 校验..."
    python3 "${CHECKER}"
elif command -v python >/dev/null 2>&1 && python -c "import tomllib" >/dev/null 2>&1; then
    echo "使用本地 python 校验..."
    python "${CHECKER}"
else
    echo "本地无 Python 3.11+，使用容器（${PYTHON_IMAGE}）校验..."
    # 显式 --entrypoint python：镜像若自带入口（如 uv 镜像）也不会篡改执行命令
    docker run --rm --entrypoint python -v "$(pwd):/app" -w /app "${PYTHON_IMAGE}" \
        /app/devops/scripts/check_app.py
fi
