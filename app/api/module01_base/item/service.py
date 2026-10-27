from typing import List, Dict, Optional, Tuple
from datetime import datetime
from app.api.module01_base.item.schema import ItemCreate, ItemUpdate, ItemPatch
from app.api.module01_base.item.static_data import items_db, current_id, INITIAL_ITEMS


class ItemService:
    """项目管理服务类"""

    @staticmethod
    def get_all_items() -> List[Dict]:
        """获取所有项目列表"""
        return items_db.copy()

    @staticmethod
    def get_item_by_id(item_id: int) -> Optional[Dict]:
        """根据ID获取单个项目"""
        for item in items_db:
            if item["id"] == item_id:
                return item.copy()
        return None

    @staticmethod
    def create_item(item_data: ItemCreate) -> Dict:
        """创建新项目"""
        global current_id

        # 生成新ID
        current_id += 1
        new_id = current_id

        # 创建时间戳
        timestamp = datetime.now().isoformat()

        # 构建新项目数据
        new_item = {
            "id": new_id,
            "name": item_data.name,
            "description": item_data.description,
            "status": item_data.status,
            "priority": item_data.priority,
            "due_date": item_data.due_date,
            "assigned_to": item_data.assigned_to,
            "created_at": timestamp,
            "updated_at": timestamp
        }

        # 添加到数据库
        items_db.append(new_item)

        return new_item.copy()

    @staticmethod
    def update_item(item_id: int, item_data: ItemUpdate) -> Tuple[bool, Optional[Dict]]:
        """全量更新项目"""
        for index, item in enumerate(items_db):
            if item["id"] == item_id:
                # 更新时间戳
                timestamp = datetime.now().isoformat()

                # 全量更新
                updated_item = {
                    "id": item_id,
                    "name": item_data.name,
                    "description": item_data.description,
                    "status": item_data.status,
                    "priority": item_data.priority,
                    "due_date": item_data.due_date,
                    "assigned_to": item_data.assigned_to,
                    "created_at": item["created_at"],  # 保留创建时间
                    "updated_at": timestamp
                }

                # 更新数据
                items_db[index] = updated_item
                return True, updated_item.copy()

        return False, None

    @staticmethod
    def patch_item(item_id: int, patch_data: ItemPatch) -> Tuple[bool, Optional[Dict]]:
        """部分更新项目"""
        for index, item in enumerate(items_db):
            if item["id"] == item_id:
                # 创建更新后的数据副本
                updated_item = item.copy()

                # 只更新提供的字段
                if patch_data.name is not None:
                    updated_item["name"] = patch_data.name
                if patch_data.description is not None:
                    updated_item["description"] = patch_data.description
                if patch_data.status is not None:
                    updated_item["status"] = patch_data.status
                if patch_data.priority is not None:
                    updated_item["priority"] = patch_data.priority
                if patch_data.due_date is not None:
                    updated_item["due_date"] = patch_data.due_date
                if patch_data.assigned_to is not None:
                    updated_item["assigned_to"] = patch_data.assigned_to

                # 更新时间戳
                updated_item["updated_at"] = datetime.now().isoformat()

                # 更新数据
                items_db[index] = updated_item
                return True, updated_item.copy()

        return False, None

    @staticmethod
    def delete_item(item_id: int) -> bool:
        """删除项目"""
        for index, item in enumerate(items_db):
            if item["id"] == item_id:
                items_db.pop(index)
                return True
        return False

    @staticmethod
    def reset_data() -> None:
        """重置数据到初始状态（用于测试）"""
        # global items_db, current_id
        # from static_data import INITIAL_ITEMS
        global current_id

        items_db.clear()
        items_db.extend(INITIAL_ITEMS)
        current_id = len(INITIAL_ITEMS)