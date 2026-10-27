from typing import Annotated

from fastapi import APIRouter, Path, Depends, Body, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.api.v1.module_system.user.schema import UserOutSchema, UserQueryParam, UserCreateSchema, UserUpdateSchema
from app.api.v1.module_system.user.service import UserService
from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import PaginationQueryParam, PageResultSchema
from app.core.dependencies import db_getter
from app.core.router_class import OperationLogRoute

UserRouter = APIRouter(route_class=OperationLogRoute, prefix="/users", tags=["101-用户管理"])


@UserRouter.get("/list", summary="01-分页查询用户", response_model=ResponseSchema[PageResultSchema[UserOutSchema]])
async def get_user_list_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        page: Annotated[PaginationQueryParam, Depends()],
        search: Annotated[UserQueryParam, Query()],
) -> JSONResponse:
    result_dict = await UserService(db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result_dict, msg="查询用户成功")


@UserRouter.get("/detail/{id}", summary="02-查询用户详情", response_model=ResponseSchema[UserOutSchema])
async def get_user_detail_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="用户ID", ge=1)],
) -> JSONResponse:
    result_dict = await UserService(db).detail(id=id)
    return SuccessResponse(data=result_dict, msg="获取用户详情成功")


@UserRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="03-创建用户",
                 response_model=ResponseSchema[UserOutSchema])
async def create_user_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[UserCreateSchema, Body(description="创建用户参数")],
) -> JSONResponse:
    result_dict = await UserService(db).create(data=data)
    return SuccessResponse(data=result_dict, msg="创建用户成功")


@UserRouter.put("/update/{id}", summary="04-修改用户", response_model=ResponseSchema[UserOutSchema])
async def update_user_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="用户ID")],
        data: Annotated[UserUpdateSchema, Body(description="修改用户参数")],
) -> JSONResponse:
    result_dict = await UserService(db).update(id=id, data=data)
    return SuccessResponse(data=result_dict, msg="修改用户成功")


@UserRouter.delete("/delete", summary="05-删除用户", response_model=ResponseSchema[None])
async def delete_user_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await UserService(db).delete(ids=ids)
    return SuccessResponse(msg="删除用户成功")
