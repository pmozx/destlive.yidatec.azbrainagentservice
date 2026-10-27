from typing import Optional
from pydantic import BaseModel, Field


class ItemBase(BaseModel):
    """项目基础模型"""
    name: str = Field(..., description="项目名称", min_length=1, max_length=100)
    description: Optional[str] = Field(None, description="项目描述", max_length=500)
    status: str = Field(default="pending", description="项目状态: pending/active/completed/archived")
    priority: str = Field(default="medium", description="优先级: low/medium/high/urgent")
    due_date: Optional[str] = Field(None, description="截止日期，格式: YYYY-MM-DD")
    assigned_to: Optional[str] = Field(None, description="负责人")


class ItemCreate(ItemBase):
    """创建项目请求模型"""
    pass


class ItemUpdate(ItemBase):
    """全量更新项目请求模型"""
    pass


class ItemPatch(BaseModel):
    """部分更新项目请求模型（所有字段可选）"""
    name: Optional[str] = Field(None, description="项目名称", min_length=1, max_length=100)
    description: Optional[str] = Field(None, description="项目描述", max_length=500)
    status: Optional[str] = Field(None, description="项目状态: pending/active/completed/archived")
    priority: Optional[str] = Field(None, description="优先级: low/medium/high/urgent")
    due_date: Optional[str] = Field(None, description="截止日期，格式: YYYY-MM-DD")
    assigned_to: Optional[str] = Field(None, description="负责人")


class Item(ItemBase):
    """项目响应模型"""
    id: int = Field(..., description="项目ID")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")

    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "name": "测试项目",
                "description": "这是一个测试项目",
                "status": "active",
                "priority": "high",
                "due_date": "2026-12-31",
                "assigned_to": "张三",
                "created_at": "2026-08-21T10:00:00",
                "updated_at": "2026-08-21T10:00:00"
            }
        }