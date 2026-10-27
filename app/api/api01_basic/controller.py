from typing import Annotated
from fastapi import APIRouter, Path, Query
from starlette.responses import FileResponse, HTMLResponse

from app.api.api01_basic.Schema import UserRegisterModel, NewInfoResp
from app.config.path_conf import STATIC_DIR
from app.core.logger import logger

"""
fastapi api 定义路由：
方式一：使用 app 装饰器定义路由，不建议在大型项目中使用，所有路由集中在主文件，难以维护，无法模块化管理，代码臃肿，违反单一职责原则
方式二：使用 router 装饰器定义路由，然后在 app 中注册 router，项目中推荐使用，因为可以将不同模块的路由进行分类管理
"""
# 定义路由
router = APIRouter(prefix="/api01_basic", tags=["101-fastapi 路由"])


@router.get("/d01_router", summary="01-路由定义", description="01-路由定义入门定义")
async def d01_router():
    logger.info(f'访问路由 /api01_basic/d01_router')
    return {"message": "Hello World"}


@router.get("/d02_path_param/{xxx_id}", summary="02-路径参数", description="02-路径参数使用")
async def d02_path_param(
        # xxx_id:int = Path(description="id（路径参数）",gt=0,lt=100)
        xxx_id: Annotated[int, Path(description="id（路径参数）", gt=0, lt=100)]
):
    logger.info('获取到路径参数 id: {}', xxx_id)
    return {"message": f"获取到路径参数 xxx_id: {xxx_id}"}


@router.get("/d03_query_param", summary="03-查询参数", description="03-查询参数应用")
async def d03_query_param(
        skip: Annotated[int, Query(description="跳过的记录数", lt=100)] = 0,
        limit: Annotated[int, Query(description="返回的记录数")] = 10
):
    result: dict[str, str | int] = {
        "message": "Query Parameters",
        "skip": skip,
        "limit": limit
    }
    return result


@router.post("/d04_request_body", summary="04-请求体", description="04-请求体应用")
async def d04_request_body(user: UserRegisterModel) -> dict[str, str | UserRegisterModel]:
    return {
        "message": "User registered successfully",
        "user": user
    }


@router.get("/d05_response_body/{new_id}", summary="05-响应体")
async def d05_response_body(new_id: Annotated[int, Path(description="id（路径参数）", gt=0, lt=100)]) -> NewInfoResp:
    logger.info('获取到路径参数 id: {}', new_id)
    return NewInfoResp(
        id=new_id,
        title="FastAPI 介绍",
        content="FastAPI 是一个基于标准 Python 类型提示的现代、快速（高性能） Web 框架，用于使用 Python 3.7+ 构建 API。"
    )


@router.get("/d06_response_html", summary="06-响应 html", response_class=HTMLResponse)
async def d06_response_html():
    return """
    <html>
        <head>
            <title>响应 html</title>
        </head>
        <body>
            <h1>响应 html</h1>
        </body>
    </html>
    """


@router.get("/d07_response_file", summary="07-响应文件")
async def d07_response_file():
    image_file_path = STATIC_DIR / "IMAGES" / "1.jpeg"
    # logger.info("文件路径类型: {}", type(image_file_path))
    logger.info('获取到文件路径: {}', image_file_path)
    return FileResponse(image_file_path)
