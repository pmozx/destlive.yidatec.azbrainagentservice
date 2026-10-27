#!/bin/bash

# ==============================================================================
# 部署侧公共默认值与环境初始化（被 00-deploy-all.sh / manual/*.sh source）
#
#   应用运行配置的唯一来源是 backend/env/.env.<env>：部署时挂载进容器
#   /home/env/.env.<env>，由应用（pydantic-settings）自行加载，部署脚本不做
#   任何渲染 / 拆分 / 拷贝。本文件只负责部署侧变量的默认值与必需要素校验：
#
#     - 非口令项（镜像仓库地址、镜像名、端口等）默认值入库存放于此，
#       执行机可用同名环境变量覆盖（export XXX=... 后再调用脚本）；
#     - 镜像仓库口令严禁写入仓库文件/脚本：执行机一次性
#       `docker login <DOCKER_REPO_URL>` 预认证（凭证持久化于 ~/.docker/config.json），
#       或临时导出 DOCKER_REPO_USERNAME / DOCKER_REPO_PASSWORD（仅 package.sh 推送镜像时使用）。
#
#   环境矩阵：dev | test | uat | prod（与 backend/env/ 下的文件一一对应）。
# ==============================================================================

# 初始化部署环境：校验环境名、导出部署侧变量默认值、校验应用配置文件存在
#   用法: init_deploy_env <dev|test|uat|prod>
init_deploy_env() {
    local env_name="${1:-}"

    case "${env_name}" in
        dev|test|uat|prod) ;;
        *)
            echo "错误: 环境名仅支持 dev | test | uat | prod，当前值：${env_name}" >&2
            exit 1
            ;;
    esac

    export ENV_NAME="${env_name}"
    # 容器入口脚本与应用配置文件选择均依赖 ENVIRONMENT，强制以环境名为准
    export ENVIRONMENT="${env_name}"

    # 镜像仓库与应用命名（非口令，默认值可被同名环境变量覆盖）
    # TODO: 若镜像仓库地址/命名空间与下列默认值不同，导出 DOCKER_REPO_URL /
    #       DOCKER_REPO_NAMESPACE 覆盖，或在流水线变量中配置
    export DOCKER_REPO_URL="${DOCKER_REPO_URL:-crpi-7nbh1cvsjd338a37.cn-qingdao.personal.cr.aliyuncs.com}"
    export DOCKER_REPO_NAMESPACE="${DOCKER_REPO_NAMESPACE:-yidatec-mxx}"
    export IMAGE_NAME_APP="${IMAGE_NAME_APP:-${DOCKER_REPO_URL}/${DOCKER_REPO_NAMESPACE}/service-brain-agent}"
    export APP_NAME_APP="${APP_NAME_APP:-service-brain-agent}"

    # 构建相关（前后端两条链：Python 依赖走 uv.lock，前端在镜像内 npm 构建）
    export PYTHON_IMAGE="${PYTHON_IMAGE:-astral/uv:python3.12-bookworm-slim}"
    export NODE_IMAGE="${NODE_IMAGE:-node:22-alpine}"
    export NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
    # 留空表示按 uv.lock 内固化的源下载；内网构建时可导出私有源地址
    export PYPI_INDEX_URL="${PYPI_INDEX_URL:-}"

    # compose 形态
    export APP_PORT="${APP_PORT:-8002}"
    export APP_NETWORK="${APP_NETWORK:-service-brain-agent}"

    # 应用运行配置唯一来源：backend/env/.env.<env>（挂载进容器，应用自行加载）
    local project_root
    project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    export APP_ENV_FILE="${project_root}/backend/env/.env.${env_name}"
    if [ ! -f "${APP_ENV_FILE}" ]; then
        echo "错误: 缺少应用配置文件 backend/env/.env.${env_name}" >&2
        echo "      请参考 backend/env/.env.dev 创建该文件（非开发环境的口令不要提交入库）" >&2
        exit 1
    fi
}
