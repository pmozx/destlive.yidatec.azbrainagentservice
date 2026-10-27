from fastapi import APIRouter

# 定义路由
router = APIRouter(prefix="/api02_middleware",tags=["102-中间件使用"])

@router.get("/d01_middleware",summary="01-测试中间件")
async def d01_middleware():
    return {
        "message": "Hello World"
    }