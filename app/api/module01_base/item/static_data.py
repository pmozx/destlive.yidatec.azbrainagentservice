from typing import List, Dict
from datetime import datetime

# 初始化静态数据
INITIAL_ITEMS = [
    {
        "id": 1,
        "name": "项目Alpha",
        "description": "这是一个重要的Alpha项目，用于测试核心功能",
        "status": "active",
        "priority": "high",
        "due_date": "2026-09-30",
        "assigned_to": "张三",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    },
    {
        "id": 2,
        "name": "项目Beta",
        "description": "Beta项目，专注于用户界面优化",
        "status": "pending",
        "priority": "medium",
        "due_date": "2026-10-15",
        "assigned_to": "李四",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    },
    {
        "id": 3,
        "name": "项目Gamma",
        "description": "Gamma项目，后端架构重构",
        "status": "completed",
        "priority": "urgent",
        "due_date": "2026-08-15",
        "assigned_to": "王五",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    },
    {
        "id": 4,
        "name": "项目Delta",
        "description": "Delta项目，数据分析和报表系统",
        "status": "active",
        "priority": "high",
        "due_date": "2026-11-01",
        "assigned_to": "赵六",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    },
    {
        "id": 5,
        "name": "项目Epsilon",
        "description": "Epsilon项目，移动端开发",
        "status": "archived",
        "priority": "low",
        "due_date": "2026-07-01",
        "assigned_to": "钱七",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
]

# 用于存储数据的列表和ID计数器
items_db: List[Dict] = []
current_id: int = 5  # 从5开始，因为已经初始化了5个项目

# 初始化数据
items_db.extend(INITIAL_ITEMS)