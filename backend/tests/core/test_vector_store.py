"""向量层纯函数回归（优化报告 〇-A.7.1 改造配套）。

背景：``core/rag/vector_store.py`` 从 ``langchain-postgres`` 的 ``PGVector`` 改为原生 SQL 自实现，
表名由库内硬编码的 ``langchain_pg_*`` 改为 ``sbp_ai_kb_vector_collection`` / ``sbp_ai_kb_vector_embedding``。
改造后 SQL 拼接的正确性靠这几层纯函数兜底，一旦被改坏，表现是「写入成功但检索不到」这类隐性故障，
所以单独锁住：

1. ``_vector_literal``：向量转 pgvector 字面量，SQL 里再 CAST 成 ``vector``；
2. ``_split_search_filters``：把 ``{"k": v}`` 与 ``{"k": {"$in": [...]}}`` 拆成等值/集合两类过滤，
   兼容原 PGVector 的 ``$in`` 语法（调用方可能仍按旧写法传）；
3. ``_IDENTIFIER_RE``：``index_name`` 会拼进 DDL，必须拦住非法标识符；
4. ``validate_embedding``：维度不一致要在落库前拒绝，而不是让 PostgreSQL 报类型错。

本文件只测纯函数，不连数据库——向量列是 ``vector`` 类型，sqlite 建不出来。
"""

import pytest

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.rag import vector_store as vs


# ---------------------------------------------------------------- 向量字面量


def test_vector_literal_format() -> None:
    """输出 pgvector 可解析的 ``[a,b,c]``，不留空格。"""
    assert vs._vector_literal([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"


def test_vector_literal_handles_integers_and_small_values() -> None:
    """整数值省略小数点，极小值走科学计数法（两者 pgvector 都接受）。"""
    assert vs._vector_literal([1.0, -2.0]) == "[1,-2]"
    assert vs._vector_literal([0.00001234]) == "[1.234e-05]"


def test_vector_literal_empty() -> None:
    """空向量给 ``[]``，由上游维度校验负责拒绝，这里不做隐式兜底。"""
    assert vs._vector_literal([]) == "[]"


# ---------------------------------------------------------------- 过滤条件拆分


def test_split_filters_plain_equality() -> None:
    """``{"doc_id": 1}`` 走等值过滤，并把值留给 ``_where_sql`` 转字符串。"""
    equals, in_sets = vs._split_search_filters({"doc_id": 1, "page_no": None}, None)
    assert equals == {"doc_id": 1, "page_no": None}
    assert in_sets == {}


def test_split_filters_dollar_in_syntax() -> None:
    """兼容原 PGVector 的 ``{"k": {"$in": [...]}}`` 写法，拆到集合过滤。"""
    equals, in_sets = vs._split_search_filters({"doc_id": {"$in": [1, 2]}}, None)
    assert equals == {}
    assert in_sets == {"doc_id": [1, 2]}


def test_split_filters_merges_in_filters() -> None:
    """独立参数 ``in_filters`` 与 ``$in`` 语法合并，后者覆盖同名键。"""
    equals, in_sets = vs._split_search_filters(
        {"doc_id": {"$in": [3]}, "page_no": 2},
        {"space_id": [7, 8], "doc_id": [99]},
    )
    assert equals == {"page_no": 2}
    assert in_sets == {"space_id": [7, 8], "doc_id": [3]}


def test_split_filters_empty() -> None:
    """两者都不给时返回空字典，检索即不附加 WHERE 条件。"""
    assert vs._split_search_filters(None, None) == ({}, {})


# ---------------------------------------------------------------- 索引名白名单


@pytest.mark.parametrize("name", ["ix_custom_hnsw", "_idx", "ix123", "a" * 63])
def test_index_identifier_accepts_valid(name: str) -> None:
    """字母/下划线开头、只含字母数字下划线、≤63 字符。"""
    assert vs._IDENTIFIER_RE.match(name) is not None


@pytest.mark.parametrize(
    "name",
    ["1abc", "ix-embedding", "ix embedding", "ix;drop", "a" * 64, "索引"],
)
def test_index_identifier_rejects_invalid(name: str) -> None:
    """数字开头、连字符、空格、分号注入、超长、非 ASCII 一律拒绝。"""
    assert vs._IDENTIFIER_RE.match(name) is None


# ---------------------------------------------------------------- 维度校验


def test_validate_embedding_returns_floats() -> None:
    """维度正确时统一转 float（调用方可能传 Decimal / int 数组）。"""
    result = vs.validate_embedding([0] * settings.VECTOR_DIM)
    assert len(result) == settings.VECTOR_DIM
    assert all(isinstance(value, float) for value in result)


def test_validate_embedding_rejects_wrong_dimension() -> None:
    """维度不符抛 400，并且错误信息里带上期望值与实际值，便于定位模型配置问题。"""
    with pytest.raises(CustomException) as excinfo:
        vs.validate_embedding([0.1] * (settings.VECTOR_DIM - 1))
    assert excinfo.value.status_code == 400
    assert str(settings.VECTOR_DIM) in excinfo.value.msg
    assert str(settings.VECTOR_DIM - 1) in excinfo.value.msg


# ---------------------------------------------------------------- 表名口径


def test_table_names_follow_naming_rule() -> None:
    """表名必须是 ``sbp_ai_<域>_<业务名>``，防止回退到 langchain_pg_* 前缀。"""
    for table in (vs.COLLECTION_TABLE, vs.EMBEDDING_TABLE):
        assert table.startswith("sbp_ai_kb_"), table
        assert len(table.encode("utf-8")) <= 63, table
        assert table != "langchain_pg_collection"
        assert table != "langchain_pg_embedding"
