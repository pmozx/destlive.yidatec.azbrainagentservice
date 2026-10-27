from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String
from app.core.base_model import ModelMixin

class UserModel(ModelMixin):
    __tablename__: str = "sys_user"
    __table_args__: dict[str, str] = {"comment": "用户表"}
    username: Mapped[str] = mapped_column(String(255),nullable=False, comment="用户名")
    password: Mapped[str] = mapped_column(String(255),nullable=False, comment="密码")
    mobile: Mapped[str] = mapped_column(String(255),nullable=True, comment="手机号")
    email: Mapped[str] = mapped_column(String(255),nullable=True, comment="邮箱")
    role: Mapped[str] = mapped_column(String(255),nullable=True, comment="角色")