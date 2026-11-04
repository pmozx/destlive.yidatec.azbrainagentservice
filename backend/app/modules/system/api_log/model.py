from typing import Any

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin


class SystemApiLogModel(ModelMixin):
    """接口审计日志模型（表 sbp_ai_system_api_log）

    T1.4 在基座既有列上扩展四列（`auth_mode`/`client_id`/`request_id`/`security_check`）：
    `/v1` 契约调用没有 JWT 用户，`username` 只能填 `client_id`；安全头校验结果必须能独立取证，
    不能只留在应用日志里（ADR-13）。四列均可为空：历史行与 `/api/v1` 的 JWT 调用不回填。

    请求参数与响应报文用 ``JSONB`` 而不是 ``TEXT``：库里存的是**结构化 JSON**（不是序列化后的
    字符串），详情页才能直接展开阅读；存量库的 TEXT 列由域启动引导就地转换（见 ``bootstrap``）。
    """

    __tablename__: str = "sbp_ai_system_api_log"
    # 注意：子类一旦定义 __table_args__，就整体覆盖 Mixin 的表级索引，因此这里显式声明本表需要的索引
    # （审计查询高频按时间、路径、客户端、追踪 ID 过滤）。created_time 自带单列索引，不再重复建复合索引。
    __table_args__: tuple = (
        # 单列 path 索引替换为 (path, created_time)：联调期按路径看最近 N 条是最高频查询
        Index("ix_sbp_ai_system_api_log_path_created", "request_path", "created_time"),
        Index("ix_sbp_ai_system_api_log_username", "username"),
        Index("ix_sbp_ai_system_api_log_client_created", "client_id", "created_time"),
        Index("ix_sbp_ai_system_api_log_request_id", "request_id"),
        # JSONB 子字段的等值过滤（`security_check->>'signature_result' = 'failed'`）：
        # `->>` 返回 text，GIN(jsonb_ops) 只服务 `@>` 包含查询，这里要的必须是**表达式 B-tree**。
        # 表达式整体加一层括号，少这层 PG 会报 `syntax error at or near "->>"`。
        # 写在模型里（而非初始化脚本）是本表索引的唯一事实源：重建库时由 create_all 一并建出。
        Index("ix_sbp_ai_system_api_log_sig_result", text("((security_check ->> 'signature_result'))")),
        Index("ix_sbp_ai_system_api_log_nonce_state", text("((security_check ->> 'nonce_state'))")),
        {"comment": "接口审计日志表"},
    )

    username: Mapped[str] = mapped_column(String(64), nullable=False, default="anonymous", comment="操作人")
    status: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False, index=True, comment="操作状态(0:成功 1:失败)")
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="操作名称/异常摘要")
    request_path: Mapped[str] = mapped_column(String(255), nullable=False, comment="请求路径")
    request_method: Mapped[str] = mapped_column(String(10), nullable=False, comment="请求方式")
    request_query: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="查询字符串")
    # 结构固定为 {"body": …, "query": …, "path_params": …, "form": …}，敏感键已脱敏、文件已丢弃
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="请求参数（JSON：body/query/path_params/form）"
    )
    response_code: Mapped[int] = mapped_column(Integer, default=200, nullable=False, comment="响应状态码")
    # 仅记录 application/json 且非流式的响应；异常路径为 {"error": "…"}
    response_json: Mapped[Any | None] = mapped_column(
        JSONB, default=None, nullable=True, comment="响应报文（JSON；仅 application/json 非流式）"
    )
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
