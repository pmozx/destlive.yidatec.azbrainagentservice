"""知识检索 Controller：通道配置视图 + 双通道检索调试台。

路由前缀 ``/kb/retrieval``（域前缀由 ``app/api/v1/routers.py`` 挂载），整组接口挂
``ApiLogRoute`` 自动留痕。整套只读：检索不改数据，配置不从页面写回。

与契约 #07 ``/v1/knowledge/search`` 的差别：这里走管理端登录态，因此不需要签名五头与
``authorization`` 时效块，但必须显式给 ``tenant_id`` / ``user_id``——授权下推先于召回，
没有用户身份就算不出可检索的发布单元。
"""

from typing import Annotated

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.router_class import ApiLogRoute

from .admin_service import KbRetrievalAdminService
from .schema import KbRetrievalConfigOutSchema, KbRetrievalSearchOutSchema, KbRetrievalSearchSchema

KbRetrievalRouter = APIRouter(route_class=ApiLogRoute, prefix="/retrieval", tags=["204-知识检索"])


@KbRetrievalRouter.get(
    "/config",
    summary="01-查询检索通道配置",
    response_model=ResponseSchema[KbRetrievalConfigOutSchema],
)
async def get_retrieval_config_controller() -> JSONResponse:
    result = KbRetrievalAdminService().config()
    return SuccessResponse(data=result, msg="查询检索通道配置成功")


@KbRetrievalRouter.post(
    "/search",
    summary="02-执行知识检索（关键词 / 自然语言）",
    response_model=ResponseSchema[KbRetrievalSearchOutSchema],
)
async def search_retrieval_controller(
    data: Annotated[KbRetrievalSearchSchema, Body(description="检索条件")],
) -> JSONResponse:
    result = await KbRetrievalAdminService().search(data)
    return SuccessResponse(data=result, msg="知识检索完成")
