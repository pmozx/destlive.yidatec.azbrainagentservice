"""v1 路由总表：域前缀 → 该域 controller 清单（唯一路由事实来源）。

新增业务模块时：在 app/modules/<域>/<功能>/controller.py 定义 Router，
再到此表登记即可，禁止在 create_app 中散落 include_router。

对外前缀统一为 /api/v1（前端 axios baseURL、Vite 代理键与之一致）；
/docs、/openapi.json、/healthz 等应用级入口仍在根路径，不参与该前缀。
"""

from fastapi import APIRouter

from app.modules.system.log.controller import LogRouter

API_PREFIX = "/api/v1"

DOMAIN_CONTROLLERS: dict[str, list[APIRouter]] = {
    "/system": [
        LogRouter,
    ],
}

api_v1 = APIRouter(prefix=API_PREFIX)
for prefix, controllers in DOMAIN_CONTROLLERS.items():
    router = APIRouter(prefix=prefix)
    for controller in controllers:
        router.include_router(controller)
    api_v1.include_router(router)
