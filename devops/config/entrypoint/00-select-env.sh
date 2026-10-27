#!/bin/sh
# ==============================================================================
# 容器入口：按 ENVIRONMENT 选择运行环境后启动服务
#
#   必需变量：ENVIRONMENT（dev | test | uat | prod）
#   —— 部署侧把应用配置 env/.env.<ENVIRONMENT> 原样挂载到
#      /home/env/.env.<ENVIRONMENT>（compose bind mount），应用内的
#      app/config/setting.py 读取操作系统环境变量 ENVIRONMENT 后自行加载该文件；
#      本脚本只做合法性校验与启动参数下传，不解析、不渲染配置文件。
#
#   启动方式与 main.py 等价（工厂模式），但显式关掉热重载、固定监听 0.0.0.0：
#   main.py 面向本地开发（reload=True），容器内不可用。
#
#   容器内端口固定 8002（与 main.py 默认一致）；宿主机暴露端口由 compose 的
#   APP_PORT 控制。env 文件中的 HOST / PORT / RELOAD 仅对宿主机直跑生效。
# ==============================================================================
set -e

CONTAINER_PORT=8002

case "${ENVIRONMENT:-}" in
    dev|test|uat|prod) ;;
    "")
        echo "[select-env] 错误: ENVIRONMENT 未设置（可选值：dev | test | uat | prod）" >&2
        exit 1
        ;;
    *)
        echo "[select-env] 错误: ENVIRONMENT 取值非法: ${ENVIRONMENT}（可选值：dev | test | uat | prod）" >&2
        exit 1
        ;;
esac

if [ ! -f "/home/env/.env.${ENVIRONMENT}" ]; then
    echo "[select-env] 错误: 缺少挂载的配置文件 /home/env/.env.${ENVIRONMENT}" >&2
    echo "[select-env]       请检查 compose 的 volumes 是否把 env/.env.${ENVIRONMENT} 挂载进容器" >&2
    exit 1
fi

if [ ! -f /home/main.py ] || [ ! -d /home/app ]; then
    echo "[select-env] 错误: /home/main.py 或 /home/app 不存在，镜像内容不完整" >&2
    exit 1
fi

echo "[select-env] 启动环境: ${ENVIRONMENT}"
echo "[select-env] 监听地址: 0.0.0.0:${CONTAINER_PORT}（宿主端口见 compose 的 APP_PORT）"

cd /home
exec python -m uvicorn app:create_app \
    --factory \
    --host 0.0.0.0 \
    --port "${CONTAINER_PORT}" \
    --proxy-headers
