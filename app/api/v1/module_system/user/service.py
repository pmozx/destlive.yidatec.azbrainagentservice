from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_system.user.crud import UserCRUD
from app.api.v1.module_system.user.schema import UserOutSchema, UserQueryParam, UserCreateSchema, UserUpdateSchema
from app.core.base_schema import PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict
from app.utils.password_util import PwdUtil


class UserService:
    """用户管理服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def detail(self, id: int) -> UserOutSchema:
        user = await UserCRUD(self.db).get_or_404(id=id, preload=[])
        result = UserOutSchema.model_validate(user)
        return result

    async def get_list(
        self,
        search: UserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> list[UserOutSchema]:
        user_list = await UserCRUD(self.db).get_list(search=search_to_dict(search), order_by=order_by, preload=[])
        result = [UserOutSchema.model_validate(user) for user in user_list]
        return result

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: UserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[UserOutSchema]:
        offset = (page_no - 1) * page_size
        page_result = await UserCRUD(self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
            preload= [],
        )
        items: list[UserOutSchema] = []
        for user in page_result.items:
            item = UserOutSchema.model_validate(user)
            items.append(item)
        page_result.items = items
        return page_result  # type: ignore[return-value]

    async def create(self, data: UserCreateSchema) -> UserOutSchema:
        if data.password:
            data.password = PwdUtil.hash_password(password=data.password)
        create_data = data.model_dump(exclude_none=True, exclude={"role_ids", "position_ids"})
        new_user = await UserCRUD(self.db).create(data=create_data)
        return await self.detail(id=new_user.id)

    async def update(self, id: int, data: UserUpdateSchema) -> UserOutSchema:
        if data.username:
            if exist_user := await UserCRUD(self.db).get(username=data.username):
                if exist_user.id != id:
                    raise CustomException(msg="更新失败，账号已存在")

        if data.mobile:
            if exist_mobile := await UserCRUD(self.db).get(mobile=data.mobile):
                if exist_mobile.id != id:
                    raise CustomException(msg="该数据已存在")
        if data.email:
            if exist_email := await UserCRUD(self.db).get(email=data.email):
                if exist_email.id != id:
                    raise CustomException(msg="该数据已存在")


        update_data = data.model_dump(exclude_unset=True, exclude_none=True, exclude={"role_ids", "position_ids"})
        await UserCRUD(self.db).update(id=id, data=update_data)
        return await self.detail(id=id)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        users = await UserCRUD(self.db).get_list(search={"id": ("in", ids)})
        user_map = {u.id: u for u in users}
        errors: list[str] = []
        for uid in ids:
            user = user_map.get(uid)
            if not user:
                errors.append(f"用户[{uid}]不存在")
                continue
        if errors:
            raise CustomException(msg="; ".join(errors))

        await UserCRUD(self.db).delete(ids=ids)
