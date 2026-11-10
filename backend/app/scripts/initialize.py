"""初始化数据库基础数据（种子）。

机制与基座 ``fastapi_admin_backend/app/scripts/initialize.py`` 一致：``sql/`` 下一张表
一个同名 JSON（文件名即 ``Model.__tablename__``），启动期按 ``prepare_init_models`` 的
依赖顺序导入，日志沿用 ✅ / ⏭️ 口径。

与基座的两点差异：
1. 建表不使用迁移：本项目表结构随迭代频繁调整，由各域 bootstrap 在启动期
   ``checkfirst`` 幂等建表（见 ``app/modules/kb/bootstrap.py``）；
   ``init_db`` 里仍补一次 ``ensure_tables``，让本脚本可脱离 lifespan 单独执行。
2. 种子表之间存在外键依赖时（如子表引用父表编码），在 ``__init_data`` 里登记专用处理函数。

幂等口径与基座完全一致：**表非空即整表跳过**，只对空表导入种子。已有数据一律不插入、
不覆盖，也不补齐——页面上的改名、改密级、改排序不会被启动流程抹掉，被删除的种子行
同样不会在重启时「复活」。后续新增种子行只对全新空库生效，存量库需手工补数据。

**当前种子表**：``sbp_ai_system_model_config``（对话 / 嵌入 / 重排序三行）。同名 JSON 只
固化与环境无关的稳定项——模型名、默认参数、默认标记；``base_url`` / ``api_key`` /
``model_provider`` 留空，运行时按「行值 or env」回落，既满足「配置集中到表」，又不会把某个
环境的内网地址与密钥带进仓库。原空间/分类两张种子表随 ``integration_kb`` 模块删除，对应
JSON 已一并清理。

新增种子表的做法不变：

1. 放 ``sql/<表名>.json``（文件名必须等于 ``Model.__tablename__``）；
2. 把 Model 类追加到 ``prepare_init_models``（按外键依赖排序，被引用表在前）。
默认走「表为空则批量插入、非空则跳过」的通用分支（同基座 ``model(**item)``）；
需要外键/编码解析的表才在 ``__init_data`` 里登记专用处理函数。
"""

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.path_conf import SCRIPT_DIR
from app.core.agents.registry import invalidate_model_config_cache
from app.core.database import async_session_factory
from app.core.logger import logger
from app.modules.kb.bootstrap import ensure_tables
from app.modules.system.model_config.model import SystemModelConfigModel


class InitializeData:
    """初始化数据库基础数据"""

    # 按依赖关系排序（被引用表在前）；新增种子表把 Model 追加到这里即可
    prepare_init_models: list[type] = [SystemModelConfigModel]

    async def init_db(self) -> None:
        """建表并导入种子数据（异常上抛，由调用方决定是否阻断启动）"""
        await ensure_tables()
        if not self.prepare_init_models:
            logger.info("⏭️ 未登记种子表，跳过种子数据初始化")
            return
        async with async_session_factory() as db, db.begin():
            await self.__init_data(db)

    async def __init_data(self, db: AsyncSession) -> None:
        """逐表导入种子数据：表为空才插入，非空则整表跳过（基座口径）"""
        created_total = 0
        skipped: list[str] = []
        for model in self.prepare_init_models:
            table_name = model.__tablename__

            data = self.__load_json(table_name)
            if not data:
                logger.info("⏭️ 跳过 {} 表，无初始化数据", table_name)
                continue

            # 已有数据则整表跳过：存量库不插入、不覆盖、不补齐，避免干扰管理员的既有配置
            count = await db.execute(select(func.count()).select_from(model))
            if count.scalar():
                skipped.append(table_name)
                continue

            # 默认分支（同基座）：需要外键/编码解析时在这里登记专用构造函数
            objs = [model(**item) for item in data]

            if not objs:
                logger.info("⏭️ 跳过 {} 表数据初始化（无有效数据）", table_name)
                continue

            db.add_all(objs)
            await db.flush()
            created_total += len(objs)
            logger.info("✅ 已向 {} 表写入种子数据 {} 条", table_name, len(objs))

        if skipped:
            logger.info("⏭️ {} 张表已有数据，跳过初始化：{}", len(skipped), ", ".join(skipped))
        if created_total:
            # 快照在 lifespan 里早于种子导入加载（见 app/__init__.py），这里不置过期的话，
            # 嵌入 / 重排序客户端会一直读空快照按 env 跑，页面看到有行却「没生效」
            invalidate_model_config_cache()
            logger.info("==== 种子数据初始化完成（新增 {} 条，模型配置快照已置过期）====", created_total)

    @staticmethod
    def __load_json(filename: str) -> list[dict[str, Any]]:
        """读取并解析种子数据 JSON 文件（不存在则返回空列表）"""
        json_path = SCRIPT_DIR / f"{filename}.json"
        if not json_path.exists():
            return []

        try:
            with open(json_path, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error("❌️ 解析 {} 失败: {}", json_path, e)
            raise
        except Exception as e:
            logger.error("❌️ 读取 {} 失败: {}", json_path, e)
            raise
