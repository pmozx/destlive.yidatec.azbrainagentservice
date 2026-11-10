"""模型配置模块：表 ``sbp_ai_system_model_config``，model/schema/crud/service/controller 五层分文件。

⚠️ 本包 ``__init__`` 刻意不导出 service / controller：``app.core.agents.registry`` 会反向
导入 ``.crud``，若这里再导出 service，就形成 ``service → registry → crud → 包 __init__
→ service`` 的循环导入（表现为启动期 ImportError，报错位置还指向 registry）。需要时按完整
路径导入：``from app.modules.system.model_config.service import SystemModelConfigService``。
"""
