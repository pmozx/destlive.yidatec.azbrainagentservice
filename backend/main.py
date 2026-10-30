import uvicorn

from app.config.setting import settings


if __name__ == "__main__":
    """
    项目启动命令（工作目录为后端根目录 backend/）：
     - 方式一：直接运行 main 文件：python main.py
     - 方式二：使用 uvicorn 启动：uvicorn app:create_app --host 0.0.0.0 --port 8002 --reload 
    """
    uvicorn.run(
        "app:create_app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
        factory=True,
        # log_config=None,
    )