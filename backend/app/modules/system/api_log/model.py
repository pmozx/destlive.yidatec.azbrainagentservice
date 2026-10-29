from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.enums import DatabaseTypeEnum
from app.config.setting import settings
from app.core.base_model import ModelMixin


def get_api_log_text_column_type():
    """按数据库类型选择大文本列类型（MySQL 用 LONGTEXT，避免响应体超长被截断）。"""
    if settings.DATABASE_TYPE == DatabaseTypeEnum.MYSQL.value:
        from sqlalchemy.dialects.mysql import LONGTEXT

        return LONGTEXT
    if settings.DATABASE_TYPE == DatabaseTypeEnum.POSTGRES.value:
        from sqlalchemy.dialects.postgresql import TEXT

        return TEXT
    return Text


class SystemApiLogModel(ModelMixin):
    """接口审计日志模型（表 sbp_ai_system_api_log）

    T1.4 在基座既有列上扩展四列（`auth_mode`/`client_id`/`request_id`/`security_check`）：
    `/v1` 契约调用没有 JWT 用户，`username` 只能填 `client_id`；安全头校验结果必须能独立取证，
    不能只留在应用日志里（ADR-13）。四列均可为空：历史行与 `/api/v1` 的 JWT 调用不回填。
    """

    __tablename__: str = "sbp_ai_system_api_log"
    # 注意：子类一旦定义 __table_args__，Mixin 里基于 status/created_time 的复合索引会被整体覆盖，
    # 因此这里显式声明本表需要的索引（审计查询高频按时间、路径、客户端、追踪 ID 过滤）。
    __table_args__: tuple = (
        Index("ix_sbp_ai_system_api_log_created", "created_time", "is_deleted"),
        # 单列 path 索引替换为 (path, created_time)：联调期按路径看最近 N 条是最高频查询
        Index("ix_sbp_ai_system_api_log_path_created", "request_path", "created_time"),
        Index("ix_sbp_ai_system_api_log_username", "username"),
        Index("ix_sbp_ai_system_api_log_client_created", "client_id", "created_time"),
        Index("ix_sbp_ai_system_api_log_request_id", "request_id"),
        {"comment": "接口审计日志表"},
    )

    username: Mapped[str] = mapped_column(String(64), nullable=False, default="anonymous", comment="操作人")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True, comment="操作状态(0:成功 1:失败)")
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="操作名称/异常摘要")
    request_path: Mapped[str] = mapped_column(String(255), nullable=False, comment="请求路径")
    request_method: Mapped[str] = mapped_column(String(10), nullable=False, comment="请求方式")
    request_query: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="查询字符串")
    request_payload: Mapped[str | None] = mapped_column(get_api_log_text_column_type(), default=None, nullable=True, comment="请求参数")
    response_code: Mapped[int] = mapped_column(Integer, default=200, nullable=False, comment="响应状态码")
    response_json: Mapped[str | None] = mapped_column(get_api_log_text_column_type(), default=None, nullable=True, comment="响应体")
    process_time: Mapped[str | None] = mapped_column(String(20), default=None, nullable=True, comment="处理耗时(毫秒)")
    request_ip: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, index=True, comment="请求IP")
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True, comment="浏览器 UA")
    # ---- T1.4 新增四列 ----
    auth_mode: Mapped[str | None] = mapped_column(String(8), default=None, nullable=True, comment="鉴权方式：jwt/hmac")
    client_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="HMAC 调用方标识")
    request_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="契约追踪 ID")
    security_check: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="安全头校验明细（签名只存摘要，nonce 全量留存）"
    )
