"""知识分类接口（/integration-kb/category/*）。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.dependencies import db_getter
from app.core.router_class import ApiLogRoute

from .schema import CategoryCreateSchema, CategoryOutSchema, CategoryQueryParam, CategoryUpdateSchema
from .service import IntegrationKbCategoryService

IntegrationKbCategoryRouter = APIRouter(route_class=ApiLogRoute, prefix="/category", tags=["202-知识库-知识分类"])


@IntegrationKbCategoryRouter.get("/tree", summary="01-查询知识分类树", response_model=ResponseSchema[list[CategoryOutSchema]])
async def get_category_tree_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        search: Annotated[CategoryQueryParam, Query()],
) -> JSONResponse:
    result_dict = await IntegrationKbCategoryService(db).tree(search=search)
    return SuccessResponse(data=result_dict, msg="查询知识分类树成功")


@IntegrationKbCategoryRouter.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    summary="02-创建知识分类",
    response_model=ResponseSchema[CategoryOutSchema],
)
async def create_category_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[CategoryCreateSchema, Body(description="知识分类创建参数")],
) -> JSONResponse:
    result_dict = await IntegrationKbCategoryService(db).create(data=data)
    return SuccessResponse(data=result_dict, msg="创建知识分类成功")


@IntegrationKbCategoryRouter.put("/update/{id}", summary="03-修改知识分类", response_model=ResponseSchema[CategoryOutSchema])
async def update_category_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        id: Annotated[int, Path(description="分类ID", ge=1)],
        data: Annotated[CategoryUpdateSchema, Body(description="知识分类修改参数")],
) -> JSONResponse:
    result_dict = await IntegrationKbCategoryService(db).update(id=id, data=data)
    return SuccessResponse(data=result_dict, msg="修改知识分类成功")


@IntegrationKbCategoryRouter.delete("/delete", summary="04-删除知识分类", response_model=ResponseSchema[None])
async def delete_category_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        ids: Annotated[list[int], Body(description="分类ID列表")],
) -> JSONResponse:
    await IntegrationKbCategoryService(db).delete(ids=ids)
    return SuccessResponse(msg="删除知识分类成功")
