"""system 模块表模型聚合入口（一表一目录）。

模型已按表拆分到各子模块（``nonce`` / ``idempotency`` / ``request_log`` /
``prompt_template`` / ``auth_cache``）；这里只做统一导出，保持
``from app.modules.system.models import ...`` 与启动期建表/测试的既有引用不变。

导入本文件即把 5 张表注册进 ``MappedBase.metadata``：启动期建表与测试都依赖这一点，
少了这里，建表会"看不到表"却没有任何报错。
"""

from .auth_cache.model import SystemAuthCacheModel
from .idempotency.model import SystemIdempotencyModel
from .nonce.model import SystemNonceModel
from .prompt_template.model import SystemPromptTemplateModel
from .request_log.model import SystemRequestLogModel

__all__ = [
    "SystemAuthCacheModel",
    "SystemIdempotencyModel",
    "SystemNonceModel",
    "SystemPromptTemplateModel",
    "SystemRequestLogModel",
]
