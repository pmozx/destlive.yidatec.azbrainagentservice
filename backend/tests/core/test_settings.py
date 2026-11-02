"""配置校验的回归测试（报告 P0-6 与驱动映射收敛）。

三件事都关乎「启动前就该失败」：数据库口令留空、非 sqlite 场景必须报错；
驱动映射只允许存在一份事实源（``DatabaseTypeEnum``），否则各处拼接 URL 会漂移。
"""

import pytest
from pydantic import ValidationError

from app.common.enums import DatabaseTypeEnum
from app.config.setting import Settings


def test_empty_database_password_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """口令留空且不是 sqlite：启动期就报错，而不是等到第一次建连。"""
    monkeypatch.setenv("DATABASE_TYPE", DatabaseTypeEnum.POSTGRES.value)
    monkeypatch.setenv("DATABASE_PASSWORD", "")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    assert "DATABASE_PASSWORD" in str(excinfo.value)


def test_empty_password_allowed_for_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """sqlite 不需要口令，不能被同一条校验误伤。"""
    monkeypatch.setenv("DATABASE_TYPE", DatabaseTypeEnum.SQLITE.value)
    monkeypatch.setenv("DATABASE_PASSWORD", "")

    assert Settings().DATABASE_PASSWORD == ""


def test_driver_mapping_has_single_source() -> None:
    """驱动映射只留 enums 一份：三种类型的取值与历史 URL 口径一致，未知值回落 postgres。"""
    assert DatabaseTypeEnum.async_driver_for("postgres") == "postgresql+asyncpg"
    assert DatabaseTypeEnum.async_driver_for("mysql") == "mysql+aiomysql"
    assert DatabaseTypeEnum.async_driver_for("sqlite") == "sqlite+aiosqlite"
    assert DatabaseTypeEnum.async_driver_for("oracle") == "postgresql+asyncpg"
