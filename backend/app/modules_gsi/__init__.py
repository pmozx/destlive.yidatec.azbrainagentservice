"""GSI 业务数据只读包（``modules_gsi``）。

总入口为 ``gsi``，但 GSI 业务表均以 ``sbp_`` 开头，因此 controller / service / crud / model
的类名统一使用 **Sbp** 前缀（如 ``SbpSysOrgModel``、``SbpSysOrgCRUD``、``SbpSysOrgService``、
``SbpSysOrgRouter``）。访问路径按「模块名 / 业务名」组织：

- ``app.modules_gsi.sys``：``org`` / ``org_member`` / ``audience_team`` / ``audience_team_member`` /
  ``user`` / ``rank``
- ``app.modules_gsi.km``：``doc`` / ``doc_publication`` / ``doc_record``
- ``app.modules_gsi.faq``：``faq`` / ``faq_publication`` / ``faq_record``

每个业务目录固定为 ``model.py / crud.py / schema.py / service.py / controller.py``，
严格遵循 controller → service → crud → model 分层；只读接口登记在
``app/api/v1/routers.py`` 的 ``/gsi`` 域下（规则 F1 / F2）。

约定：

1. **只读**：只有查询与分页，没有 create / update / delete；
2. **不建表**：模型使用独立的 ``SbpBase.metadata``，``app.bootstrap`` 不登记本包，
   项目启动时不会按模型初始化任何 GSI 表；表结构以 GSI 侧 Flyway 为准
   （基线见 ``00-document/.../02-服务大脑GSI业务系统表结构.sql``）；
3. **最小字段**：只映射契约允许读取的列，敏感字段不进入模型，查询不会带出。
"""

from .base import SbpBase, SbpReadCRUD

__all__ = ["SbpBase", "SbpReadCRUD"]
