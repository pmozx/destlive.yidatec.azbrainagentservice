from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends

from app.core.dependencies import common_parameters, CommonParameters
from app.core.logger import logger

router = APIRouter(prefix="/api03_depends",tags=["103-依赖项应用"])

@router.get("/d01_common_parameters",summary="01-公共参数依赖项(基于函数)")
async def d01_common_parameters(
        common_params: Annotated[Dict[str, Any], Depends(common_parameters)]
):
    logger.info(f"common_params: {common_params}")
    return common_params


@router.get("/d02_common_parameters",summary="02-公共参数依赖项(基于类)")
async def d02_common_parameters(
        common_params: Annotated[CommonParameters, Depends()]
):
    logger.info(f"common_params: {common_params}")
    return common_params