"""兼容层：页码定位能力已迁至 ``app.utils.page_locator``，此处只做转发。

新代码请直接引用 ``app.utils.page_locator``，不要再依赖本模块。
"""

from app.utils.page_locator import PageLocator, build_page_map, resolve_page_no

__all__ = ["PageLocator", "build_page_map", "resolve_page_no"]
