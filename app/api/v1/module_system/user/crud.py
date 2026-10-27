from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase

from .model import UserModel
from .schema import UserCreateSchema, UserUpdateSchema


class UserCRUD(CRUDBase[UserModel, UserCreateSchema, UserUpdateSchema]):
    """用户模块数据层"""

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(model=UserModel, db=db)

    async def create_obj_crud(self, data: UserCreateSchema) -> UserModel | None:
        """创建用户

        参数:
        - data (UserCreateSchema): 创建模型。

        返回:
        - UserModel | None: 新建实体。
        """
        return await self.create(data=data)

    async def change_password(self, id: int, password_hash: str) -> UserModel:
        """修改用户密码

        参数:
        - id (int): 用户ID
        - password_hash (str): 密码哈希值

        返回:
        - UserModel: 更新后的用户信息
        """
        return await self.update(id=id, data=UserUpdateSchema(password=password_hash))

    async def forget_password(self, id: int, password_hash: str) -> UserModel:
        """重置密码（与 change_password 逻辑相同）"""
        return await self.change_password(id=id, password_hash=password_hash)
