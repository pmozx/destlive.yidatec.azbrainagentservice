from fastapi import FastAPI
"""
直接使用 fastapi 实例装饰器创建路由定义接口
"""
def app_router_func(app: FastAPI):
    @app.get("/api00_start/d01_hello",tags=["100-fastapi 简单示例"],summary="01-fastapi 第一个接口")
    async def d01_hello():
        return {
            "message": "hello fastAPI!"
        }