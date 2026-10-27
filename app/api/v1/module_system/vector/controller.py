from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ErrorResponse, ResponseSchema, SuccessResponse
from app.core.dependencies import db_getter
from app.core.exceptions import CustomException

from .schema import (
    ChunkDeleteSchema,
    ChunkHitSchema,
    ChunkSearchSchema,
    ChunkUpsertResultSchema,
    ChunkUpsertSchema,
    VectorStatusSchema,
)
from .service import VectorStoreService

# 刻意不挂 OperationLogRoute：写入请求体是上千维浮点数组，入库只会放大审计表体积，
# 且向量化链路的可追溯性由文档/切块自身的 created_time、updated_time 承担。
VectorRouter = APIRouter(prefix="/vector", tags=["103-向量存储"])


def _custom_error(exc: CustomException) -> JSONResponse:
    """局部转结构化错误：本项目未在 create_app 中全局注册 CustomException 处理器，
    不包一层的话维度/配置错误会变成纯文本 500，排障时拿不到原因。"""
    return ErrorResponse(msg=exc.msg, code=exc.code, status_code=exc.status_code)


@VectorRouter.get("/status", summary="01-向量存储自检", response_model=ResponseSchema[VectorStatusSchema])
async def get_vector_status_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await VectorStoreService(db).status()
    return SuccessResponse(data=result, msg="向量存储状态查询成功")


@VectorRouter.post("/chunks/upsert", summary="02-批量写入知识切块", response_model=ResponseSchema[ChunkUpsertResultSchema])
async def upsert_chunks_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[ChunkUpsertSchema, Body(description="切块列表（含向量）")],
) -> JSONResponse:
    try:
        result = await VectorStoreService(db).upsert(data)
    except CustomException as exc:
        return _custom_error(exc)
    return SuccessResponse(data=result, msg=f"已写入 {result.written} 条切块")


@VectorRouter.post("/chunks/search", summary="03-按余弦相似度检索切块", response_model=ResponseSchema[list[ChunkHitSchema]])
async def search_chunks_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[ChunkSearchSchema, Body(description="检索参数（含查询向量）")],
) -> JSONResponse:
    try:
        hits = await VectorStoreService(db).search(data)
    except CustomException as exc:
        return _custom_error(exc)
    return SuccessResponse(data=hits, msg=f"召回 {len(hits)} 条切块")


@VectorRouter.delete("/chunks", summary="04-按文档删除切块", response_model=ResponseSchema[int])
async def delete_chunks_controller(
        db: Annotated[AsyncSession, Depends(db_getter)],
        data: Annotated[ChunkDeleteSchema, Body(description="按文档删除参数")],
) -> JSONResponse:
    try:
        deleted = await VectorStoreService(db).delete(data)
    except CustomException as exc:
        return _custom_error(exc)
    return SuccessResponse(data=deleted, msg=f"已删除 {deleted} 条切块")
