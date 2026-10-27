from fastapi import APIRouter, HTTPException, status
from typing import List
from app.api.module01_base.item.schema import Item, ItemCreate, ItemUpdate, ItemPatch
from app.api.module01_base.item.service import ItemService

item_router = APIRouter(prefix="/item", tags=["mod01_项目管理"])


@item_router.get("/list", summary="01-获取项目列表",response_model=List[Item])
async def list_items():
    """获取所有项目列表"""
    items = ItemService.get_all_items()
    return items


@item_router.post("/create", summary="02-创建新项目", response_model=Item, status_code=status.HTTP_201_CREATED)
async def create_item(item_data: ItemCreate):
    """创建新项目"""
    new_item = ItemService.create_item(item_data)
    return new_item


@item_router.put("/update/{id}", summary="03-全量更新项目", response_model=Item)
async def update_item(id: int, item_data: ItemUpdate):
    """全量更新项目"""
    success, updated_item = ItemService.update_item(id, item_data)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目ID {id} 不存在"
        )
    return updated_item


@item_router.delete("/delete/{id}", summary="04-删除项目")
async def delete_item(id: int):
    """删除项目"""
    success = ItemService.delete_item(id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目ID {id} 不存在"
        )
    return {"deleted": True, "id": id}


@item_router.patch("/patch/{id}", summary="05-部分更新项目", response_model=Item)
async def patch_item(id: int, patch_data: ItemPatch):
    """部分更新项目"""
    success, patched_item = ItemService.patch_item(id, patch_data)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目ID {id} 不存在"
        )
    return patched_item


# 可选的辅助路由
@item_router.get("/{id}", summary="06-获取项目详情", response_model=Item)
async def get_item(id: int):
    """获取单个项目详情"""
    item = ItemService.get_item_by_id(id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目ID {id} 不存在"
        )
    return item


@item_router.post("/reset", summary="07-重置数据")
async def reset_data():
    """重置数据到初始状态（仅用于开发测试）"""
    ItemService.reset_data()
    return {"message": "数据已重置", "count": len(ItemService.get_all_items())}