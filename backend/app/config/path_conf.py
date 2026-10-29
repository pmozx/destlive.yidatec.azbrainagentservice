from pathlib import Path

# 后端工程根目录（仓库内为 backend/，镜像内为 /home；由本文件位置向上三级推导）
BASE_DIR = Path(__file__).parent.parent.parent

# alembic 迁移文件存放路径
ALEMBIC_VERSION_DIR = BASE_DIR / "app" / "alembic" / "versions"

# 日志文件路径
LOG_DIR = BASE_DIR / "logs"

# 静态资源目录
STATIC_DIR = BASE_DIR / "static"

# 环境配置目录
ENV_DIR = BASE_DIR / "env"

# 基础环境变量文件
BASE_ENV_FILE = ENV_DIR / ".env"

# 初始化脚本与种子数据目录（一张表一个同名 JSON，由 app/scripts/initialize.py 导入）
SCRIPT_DIR: Path = BASE_DIR / "sql"

# 模版文件配置（统一管理代码生成模板 + HTML 模板）
TEMPLATE_DIR: Path = BASE_DIR / "templates"

# 前端构建输出目录
FRONTEND_DIST_DIR: Path = BASE_DIR / "dist"

# banner.txt 文件路径
BANNER_FILE = BASE_DIR / "banner.txt"
