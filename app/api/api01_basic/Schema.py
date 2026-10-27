from pydantic import BaseModel, Field


class UserRegisterModel(BaseModel):
    username: str = Field(default="mengxingxu",min_length=2, max_length=10, description="用户名，长度要求2-10个字")
    password: str = Field(min_length=3, max_length=20, description="密码，长度要求3-20个字")


class NewInfoResp(BaseModel):
    """
    新闻信息响应体：包含 id、title、content
    """
    id: int = Field(description="新闻 id")
    title: str = Field(description="新闻标题")
    content: str = Field(description="新闻内容")