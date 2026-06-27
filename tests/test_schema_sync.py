"""Tests for app/core/schema_sync.py — dataclasses + _type_to_string + inspect_target."""

from __future__ import annotations

import pytest
from sqlmodel import SQLModel

# Import models to trigger SQLModel metadata registration
from app.models import User, Account, Course, CourseBinding, CheckInLog, InviteCode, SystemSetting, AutoCheckinConfig  # noqa: F401

from app.core.schema_sync import (
    ColumnDef,
    IndexDef,
    ForeignKeyDef,
    TableDef,
    ColumnChange,
    IndexChange,
    ForeignKeyChange,
    SchemaDiff,
    _type_to_string,
    _extract_default,
    inspect_target,
    inspect_current,
)
from sqlalchemy import String, Integer, Boolean, DateTime, Float


# ── DataClasses ──

class TestDataClasses:
    def test_column_def_defaults(self):
        c = ColumnDef(name="col1", type_str="VARCHAR(255)")
        assert c.name == "col1"
        assert c.type_str == "VARCHAR(255)"
        assert c.nullable is True
        assert c.default is None
        assert c.autoincrement is False
        assert c.primary_key is False
        assert c.comment == ""

    def test_column_def_all_fields(self):
        c = ColumnDef(
            name="id", type_str="INTEGER", nullable=False,
            default="0", autoincrement=True, primary_key=True,
            comment="primary key",
        )
        assert c.name == "id"
        assert c.type_str == "INTEGER"
        assert c.nullable is False
        assert c.default == "0"
        assert c.autoincrement is True
        assert c.primary_key is True
        assert c.comment == "primary key"

    def test_index_def_defaults(self):
        idx = IndexDef(name="ix_user_email", columns=["email"])
        assert idx.name == "ix_user_email"
        assert idx.columns == ["email"]
        assert idx.unique is False

    def test_index_def_unique(self):
        idx = IndexDef(name="uq_email", columns=["email"], unique=True)
        assert idx.unique is True

    def test_foreign_key_def_defaults(self):
        fk = ForeignKeyDef(
            columns=["user_id"],
            ref_table="user",
            ref_columns=["id"],
        )
        assert fk.columns == ["user_id"]
        assert fk.ref_table == "user"
        assert fk.ref_columns == ["id"]
        assert fk.ondelete == ""

    def test_foreign_key_def_all_fields(self):
        fk = ForeignKeyDef(
            columns=["user_id"],
            ref_table="user",
            ref_columns=["id"],
            ondelete="CASCADE",
        )
        assert fk.ondelete == "CASCADE"

    def test_table_def_defaults(self):
        cols = {"id": ColumnDef(name="id", type_str="INTEGER", primary_key=True)}
        t = TableDef(name="test", columns=cols)
        assert t.name == "test"
        assert t.columns == cols
        assert t.indexes == []
        assert t.foreign_keys == []

    def test_table_def_with_indexes_and_fks(self):
        cols = {
            "id": ColumnDef(name="id", type_str="INTEGER", primary_key=True),
            "user_id": ColumnDef(name="user_id", type_str="INTEGER"),
        }
        idx = IndexDef(name="ix_user_id", columns=["user_id"])
        fk = ForeignKeyDef(columns=["user_id"], ref_table="user", ref_columns=["id"])
        t = TableDef(name="test", columns=cols, indexes=[idx], foreign_keys=[fk])
        assert len(t.indexes) == 1
        assert t.indexes[0].name == "ix_user_id"
        assert len(t.foreign_keys) == 1
        assert t.foreign_keys[0].ref_table == "user"

    def test_column_change_defaults(self):
        cc = ColumnChange(table="user", change_type="add", column_name="email")
        assert cc.table == "user"
        assert cc.change_type == "add"
        assert cc.column_name == "email"
        assert cc.old_name is None
        assert cc.definition is None

    def test_column_change_with_definition(self):
        col = ColumnDef(name="email", type_str="VARCHAR(255)")
        cc = ColumnChange(
            table="user", change_type="alter", column_name="email",
            definition=col,
        )
        assert cc.definition is not None
        assert cc.definition.type_str == "VARCHAR(255)"

    def test_index_change(self):
        idx = IndexDef(name="ix_email", columns=["email"], unique=True)
        ic = IndexChange(table="user", change_type="add", definition=idx)
        assert ic.definition.unique is True

    def test_foreign_key_change(self):
        fk = ForeignKeyDef(columns=["user_id"], ref_table="user", ref_columns=["id"])
        fc = ForeignKeyChange(table="account", change_type="add", definition=fk)
        assert fc.definition.ref_table == "user"

    def test_schema_diff_defaults(self):
        diff = SchemaDiff()
        assert diff.tables_to_create == []
        assert diff.column_changes == []
        assert diff.index_changes == []
        assert diff.fk_changes == []


# ── _type_to_string ──

class TestTypeToString:
    @pytest.mark.parametrize(
        ("sqlalchemy_type", "expected"),
        [
            (String(), "VARCHAR"),
            (String(255), "VARCHAR(255)"),
            (Integer(), "INTEGER"),
            (Boolean(), "BOOLEAN"),
            (DateTime(), "DATETIME"),
            (Float(), "FLOAT"),
        ],
    )
    def test_type_to_string(self, sqlalchemy_type, expected):
        assert _type_to_string(sqlalchemy_type) == expected


# ── inspect_target ──

class TestInspectTarget:
    """Validates that inspect_target extracts the correct schema from SQLModel metadata."""

    def test_returns_dict_of_tabledef(self):
        result = inspect_target(SQLModel.metadata)
        assert isinstance(result, dict)
        assert all(isinstance(v, TableDef) for v in result.values())

    def test_contains_known_tables(self):
        result = inspect_target(SQLModel.metadata)
        table_names = set(result.keys())
        for name in ("user", "account", "course", "coursebinding",
                     "checkinlog", "invitecode", "systemsetting",
                     "autocheckinconfig"):
            assert name in table_names, f"Missing table: {name}"

    def test_skips_internal_tables(self):
        result = inspect_target(SQLModel.metadata)
        for name in result:
            assert not name.startswith("_"), f"Internal table leaked: {name}"

    def test_user_table_columns(self):
        result = inspect_target(SQLModel.metadata)
        user = result["user"]
        assert "id" in user.columns
        assert "email" in user.columns
        assert "password" in user.columns

        id_col = user.columns["id"]
        assert id_col.primary_key is True
        # SQLAlchemy uses the string 'auto' as a sentinel — resolves to True for single-col PK int
        assert id_col.autoincrement is True or id_col.autoincrement == 'auto'

        email_col = user.columns["email"]
        assert email_col.nullable is False  # Field() defaults nullable=False when no default
        assert "VARCHAR" in email_col.type_str

    def test_account_table_foreign_keys(self):
        """Account has no direct FK to user, but UserAccount has FKs — verify UserAccount structure."""
        result = inspect_target(SQLModel.metadata)
        ua = result.get("useraccount", result.get("useraccount"))
        # UserAccount is the link table with FKs
        ua_table = result.get("useraccount")
        if ua_table:
            assert len(ua_table.foreign_keys) >= 1

    def test_useraccount_has_foreign_keys(self):
        result = inspect_target(SQLModel.metadata)
        ua = result["useraccount"]
        fk_ref_tables = {fk.ref_table for fk in ua.foreign_keys}
        assert "user" in fk_ref_tables
        assert "account" in fk_ref_tables

    def test_indexes_present(self):
        result = inspect_target(SQLModel.metadata)
        user = result["user"]
        index_names = {idx.name for idx in user.indexes}
        assert any("email" in name.lower() for name in index_names), (
            f"Expected an index on email, got: {index_names}"
        )

    def test_all_tables_have_columns(self):
        result = inspect_target(SQLModel.metadata)
        for name, table in result.items():
            assert len(table.columns) > 0, f"Table '{name}' has zero columns"


# ── inspect_current ──

class TestInspectCurrent:
    def test_inspect_returns_created_tables(self):
        """在 SQLite 中创建所有表后，inspect_current 应返回相同表集合。"""
        from sqlmodel import create_engine
        from app.core.schema_sync import inspect_current
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        tables = inspect_current(engine)
        # 应该包含业务表名
        for name in ("user", "account", "course", "coursebinding",
                      "checkinlog", "invitecode", "systemsetting", "autocheckinconfig"):
            assert name in tables, f"Missing table: {name}"
        engine.dispose()

    def test_column_def_has_correct_fields(self):
        from sqlmodel import create_engine
        from app.core.schema_sync import inspect_current
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        tables = inspect_current(engine)
        user = tables["user"]
        assert user.columns["id"].primary_key
        assert not user.columns["email"].nullable
        assert user.name == "user"
        engine.dispose()

    def test_skips_underscore_tables(self):
        from sqlmodel import create_engine
        from sqlalchemy import text
        from app.core.schema_sync import inspect_current
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        # 手动创建 _test 表
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE _test (id INTEGER)"))
        tables = inspect_current(engine)
        assert "_test" not in tables
        engine.dispose()

    def test_empty_db_returns_empty(self):
        from sqlmodel import create_engine
        from app.core.schema_sync import inspect_current
        engine = create_engine("sqlite://", echo=False)
        tables = inspect_current(engine)
        assert isinstance(tables, dict)
        engine.dispose()

    def test_extract_default(self):
        from app.core.schema_sync import _extract_default
        assert _extract_default(None) is None
        assert _extract_default("'hello'") == "hello"
        assert _extract_default("42") == "42"
        assert _extract_default(True) == "True"


# ── detect_rename ──

class TestDetectRename:
    def test_detects_exact_rename(self):
        """username -> user_name: 同类型 VARCHAR, 编辑距离 1。"""
        from app.core.schema_sync import _detect_rename
        deleted = {"username": "VARCHAR(255)"}
        added = {"user_name": "VARCHAR(255)"}
        current_types = {"username": "VARCHAR(255)"}
        target_types = {"user_name": "VARCHAR(255)"}
        result = _detect_rename(deleted, added, current_types, target_types)
        assert result == [("username", "user_name")]

    def test_no_rename_different_type(self):
        """类型不同不应匹配。"""
        from app.core.schema_sync import _detect_rename
        deleted = {"username": "VARCHAR(255)"}
        added = {"user_count": "INTEGER"}
        current_types = {"username": "VARCHAR(255)"}
        target_types = {"user_count": "INTEGER"}
        result = _detect_rename(deleted, added, current_types, target_types)
        assert result == []

    def test_no_rename_too_different(self):
        """编辑距离 > 3 不应匹配。"""
        from app.core.schema_sync import _detect_rename
        deleted = {"a": "VARCHAR"}
        added = {"zzzzzzzzz": "VARCHAR"}
        current_types = {"a": "VARCHAR"}
        target_types = {"zzzzzzzzz": "VARCHAR"}
        result = _detect_rename(deleted, added, current_types, target_types)
        assert result == []

    def test_type_family(self):
        from app.core.schema_sync import _type_family
        assert _type_family("VARCHAR(255)") == "string"
        assert _type_family("INTEGER") == "integer"
        assert _type_family("FLOAT") == "numeric"
        assert _type_family("BOOLEAN") == "boolean"
        assert _type_family("DATETIME") == "datetime"


# ── compute_diff ──

class TestComputeDiff:
    def test_new_table(self):
        from app.core.schema_sync import compute_diff, TableDef, ColumnDef
        target = {"new_table": TableDef(name="new_table", columns={
            "id": ColumnDef(name="id", type_str="INTEGER", nullable=False, primary_key=True),
        })}
        current = {}
        diff = compute_diff(target, current)
        assert len(diff.tables_to_create) == 1
        assert diff.tables_to_create[0].name == "new_table"

    def test_new_column(self):
        from app.core.schema_sync import compute_diff, TableDef, ColumnDef
        target = {"user": TableDef(name="user", columns={
            "id": ColumnDef(name="id", type_str="INTEGER"),
            "email": ColumnDef(name="email", type_str="VARCHAR(255)"),
            "nickname": ColumnDef(name="nickname", type_str="VARCHAR(100)", nullable=True),
        })}
        current = {"user": TableDef(name="user", columns={
            "id": ColumnDef(name="id", type_str="INTEGER"),
            "email": ColumnDef(name="email", type_str="VARCHAR(255)"),
        })}
        diff = compute_diff(target, current)
        adds = [c for c in diff.column_changes if c.change_type == "add"]
        assert len(adds) == 1
        assert adds[0].column_name == "nickname"

    def test_dropped_column_is_warn_only(self):
        from app.core.schema_sync import compute_diff, TableDef, ColumnDef
        target = {"user": TableDef(name="user", columns={
            "id": ColumnDef(name="id", type_str="INTEGER"),
        })}
        current = {"user": TableDef(name="user", columns={
            "id": ColumnDef(name="id", type_str="INTEGER"),
            "obsolete": ColumnDef(name="obsolete", type_str="VARCHAR(255)"),
        })}
        diff = compute_diff(target, current)
        drops = [c for c in diff.column_changes if c.change_type == "drop"]
        assert len(drops) == 0  # 不生成 drop 操作

    def test_column_type_change(self):
        from app.core.schema_sync import compute_diff, TableDef, ColumnDef
        target = {"user": TableDef(name="user", columns={
            "name": ColumnDef(name="name", type_str="VARCHAR(100)"),
        })}
        current = {"user": TableDef(name="user", columns={
            "name": ColumnDef(name="name", type_str="VARCHAR(255)"),
        })}
        diff = compute_diff(target, current)
        alters = [c for c in diff.column_changes if c.change_type == "alter"]
        assert len(alters) == 1

    def test_no_diff_when_identical(self):
        from app.core.schema_sync import compute_diff, TableDef, ColumnDef
        td = TableDef(name="user", columns={
            "id": ColumnDef(name="id", type_str="INTEGER", nullable=False, primary_key=True),
        })
        diff = compute_diff({"user": td}, {"user": td})
        assert not diff.tables_to_create
        assert not diff.column_changes
        assert not diff.index_changes
        assert not diff.fk_changes
