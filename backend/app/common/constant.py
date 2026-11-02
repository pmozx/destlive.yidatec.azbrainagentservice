"""API 展示口径常量。

这里只保留真正被引用的日期/时间格式：原文件另有 ``CommonConstant`` / ``JobConstant`` /
``GenConstant``（约 480 行代码生成器常量），是从基座模板带过来的零引用内容，
本项目没有代码生成器与定时任务模块，已删除。
"""

# API 日期 / 时间 / 日期时间统一展示（validator、jsonable_response_content、文档约定一致）
DATE_DISPLAY_FMT = "%Y-%m-%d"
TIME_DISPLAY_FMT = "%H:%M:%S"
DATETIME_DISPLAY_FMT = "%Y-%m-%d %H:%M:%S"
