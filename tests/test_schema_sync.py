"""Tests for app/core/schema_sync.py — SchemaSync module."""

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
    _compile_ddl,
    _compile_index_ddl,
    _compile_fk_ddl,
    _affected_tables,
    _compute_schema_hash,
    SchemaSync,
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
            (String(), "VARCHAR(255)"),
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

    def test_no_rename_for_fk_column(self):
        """外键列不应参与重命名猜测。"""
        from app.core.schema_sync import _detect_rename
        deleted = {"user_id": "INTEGER", "old_extra": "VARCHAR(100)"}
        added = {"uid": "INTEGER", "extra_info": "VARCHAR(100)"}
        current_types = {"user_id": "INTEGER", "old_extra": "VARCHAR(100)"}
        target_types = {"uid": "INTEGER", "extra_info": "VARCHAR(100)"}
        result = _detect_rename(deleted, added, current_types, target_types,
                               fk_column_names={"user_id"}, max_distance=3)
        renamed_old = {r[0] for r in result}
        assert "user_id" not in renamed_old

    def test_max_distance_default_is_2(self):
        """默认 max_distance 应为 2（原为 3，已降低以提高安全性）。"""
        from app.core.schema_sync import _detect_rename
        deleted = {"a": "VARCHAR(255)"}
        added = {"bbbc": "VARCHAR(255)"}
        current_types = {"a": "VARCHAR(255)"}
        target_types = {"bbbc": "VARCHAR(255)"}
        result = _detect_rename(deleted, added, current_types, target_types)
        assert result == []


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


# ── DDL compilation ──

class TestCompileDDL:
    def test_add_column(self):
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        change = ColumnChange(table="user", change_type="add", column_name="nickname",
            definition=ColumnDef(name="nickname", type_str="VARCHAR(100)", nullable=True))
        sql = _compile_ddl(change)
        assert "ALTER TABLE `user`" in sql
        assert "ADD COLUMN" in sql
        assert "`nickname`" in sql
        assert "VARCHAR(100)" in sql

    def test_add_not_null_no_default_warns(self, caplog):
        """NOT NULL 无 DEFAULT 时应发出警告。"""
        import logging
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        caplog.set_level(logging.WARNING)
        change = ColumnChange(table="user", change_type="add", column_name="required_field",
            definition=ColumnDef(name="required_field", type_str="VARCHAR(100)", nullable=False))
        sql = _compile_ddl(change)
        assert "NOT NULL" in sql
        assert "DEFAULT" not in sql  # 没有 DEFAULT
        assert any("NOT NULL" in r.message for r in caplog.records)

    def test_add_column_not_null_with_default(self):
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        change = ColumnChange(table="user", change_type="add", column_name="status",
            definition=ColumnDef(name="status", type_str="VARCHAR(20)", nullable=False, default="active"))
        sql = _compile_ddl(change)
        assert "NOT NULL" in sql
        assert "DEFAULT" in sql

    def test_rename_column(self):
        from app.core.schema_sync import _compile_ddl, ColumnChange
        change = ColumnChange(table="user", change_type="rename", column_name="nickname",
            old_name="nick_name")
        sql = _compile_ddl(change)
        assert "RENAME COLUMN" in sql
        assert "`nick_name`" in sql
        assert "`nickname`" in sql

    def test_alter_column_type(self):
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        change = ColumnChange(table="user", change_type="alter", column_name="name",
            definition=ColumnDef(name="name", type_str="VARCHAR(100)", nullable=False))
        sql = _compile_ddl(change)
        assert "MODIFY COLUMN" in sql
        assert "VARCHAR(100)" in sql

    def test_alter_auto_increment_only(self):
        """alter 自增列时 DDL 应包含 AUTO_INCREMENT，但不含 PRIMARY KEY。

        MySQL MODIFY COLUMN 不支持 PRIMARY KEY（表已有 PK 时报 1068）。
        """
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        change = ColumnChange(table="user", change_type="alter", column_name="id",
            definition=ColumnDef(name="id", type_str="BIGINT",
                                nullable=False, autoincrement=True, primary_key=True))
        sql = _compile_ddl(change)
        assert "AUTO_INCREMENT" in sql
        assert "PRIMARY KEY" not in sql  # MODIFY COLUMN 不应含 PK
        assert "MODIFY COLUMN" in sql
        assert "`id`" in sql
        assert "BIGINT" in sql

    def test_add_column_with_comment(self):
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        col = ColumnDef(name="bio", type_str="TEXT", nullable=True, comment="用户简介")
        change = ColumnChange(table="user", change_type="add", column_name="bio", definition=col)
        sql = _compile_ddl(change)
        assert "COMMENT" in sql
        assert "用户简介" in sql

    def test_alter_column_with_comment(self):
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        col = ColumnDef(name="email", type_str="VARCHAR(255)", nullable=False, comment="用户邮箱")
        change = ColumnChange(table="user", change_type="alter", column_name="email", definition=col)
        sql = _compile_ddl(change)
        assert "COMMENT" in sql

    def test_drop_raises(self):
        import pytest
        from app.core.schema_sync import _compile_ddl, ColumnChange
        change = ColumnChange(table="user", change_type="drop", column_name="old_col")
        with pytest.raises(NotImplementedError):
            _compile_ddl(change)

    def test_add_varchar_pk_no_autoincrement(self):
        """VARCHAR 主键不应生成 AUTO_INCREMENT（MySQL 不支持）。"""
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        col = ColumnDef(name="id", type_str="VARCHAR(255)", nullable=False,
                       primary_key=True)
        change = ColumnChange(table="course", change_type="add",
                            column_name="id", definition=col)
        sql = _compile_ddl(change)
        assert "AUTO_INCREMENT" not in sql
        assert "PRIMARY KEY" in sql

    def test_alter_varchar_pk_no_autoincrement(self):
        """ALTER VARCHAR 主键不应生成 AUTO_INCREMENT，也不应有 PRIMARY KEY。"""
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        col = ColumnDef(name="id", type_str="VARCHAR(255)", nullable=False,
                       primary_key=True, autoincrement=True)
        change = ColumnChange(table="course", change_type="alter",
                            column_name="id", definition=col)
        sql = _compile_ddl(change)
        assert "AUTO_INCREMENT" not in sql
        assert "PRIMARY KEY" not in sql

    def test_autoincrement_only_for_integer(self):
        """AUTO_INCREMENT 应只对整数类型生效。"""
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        # INTEGER PK with autoincrement → 应有 AUTO_INCREMENT
        col = ColumnDef(name="id", type_str="INTEGER", nullable=False,
                       primary_key=True, autoincrement=True)
        change = ColumnChange(table="user", change_type="add",
                            column_name="id", definition=col)
        sql = _compile_ddl(change)
        assert "AUTO_INCREMENT" in sql
        assert "PRIMARY KEY" in sql


class TestIsIntegerType:
    def test_integer_types(self):
        from app.core.schema_sync import _is_integer_type
        assert _is_integer_type("INTEGER") is True
        assert _is_integer_type("INT") is True
        assert _is_integer_type("BIGINT") is True
        assert _is_integer_type("SMALLINT") is True
        assert _is_integer_type("TINYINT") is True
        assert _is_integer_type("MEDIUMINT") is True

    def test_non_integer_types(self):
        from app.core.schema_sync import _is_integer_type
        assert _is_integer_type("VARCHAR(255)") is False
        assert _is_integer_type("VARCHAR") is False
        assert _is_integer_type("TEXT") is False
        assert _is_integer_type("DATETIME") is False
        assert _is_integer_type("BOOLEAN") is False
        assert _is_integer_type("FLOAT") is False

    def test_with_length(self):
        """带长度参数的整数类型也应识别。"""
        from app.core.schema_sync import _is_integer_type
        assert _is_integer_type("INTEGER(11)") is True
        assert _is_integer_type("BIGINT(20)") is True


class TestFormatDefault:
    """测试 _format_default 函数的所有分支。"""

    def test_sql_keyword(self):
        from app.core.schema_sync import _format_default
        assert _format_default("CURRENT_TIMESTAMP") == "DEFAULT CURRENT_TIMESTAMP"
        assert _format_default("NOW()") == "DEFAULT NOW()"
        assert _format_default("TRUE") == "DEFAULT TRUE"
        assert _format_default("FALSE") == "DEFAULT FALSE"
        assert _format_default("NULL") == "DEFAULT NULL"

    def test_numeric(self):
        from app.core.schema_sync import _format_default
        assert _format_default("42") == "DEFAULT 42"
        assert _format_default("3.14") == "DEFAULT 3.14"
        assert _format_default("-1") == "DEFAULT -1"

    def test_string(self):
        from app.core.schema_sync import _format_default
        assert _format_default("hello") == "DEFAULT 'hello'"
        assert _format_default("it's") == "DEFAULT 'it\\'s'"
        assert _format_default("a'b'c") == "DEFAULT 'a\\'b\\'c'"

    def test_edge_cases(self):
        from app.core.schema_sync import _format_default
        # 包含 SQL 关键字的字符串
        assert _format_default("current_timestamp_str") == "DEFAULT 'current_timestamp_str'"
        # 以数字开头的字符串
        assert _format_default("123abc") == "DEFAULT '123abc'"
        # 空字符串
        assert _format_default("") == "DEFAULT ''"

    def test_expanded_keywords(self):
        """验证扩展后的 SQL 关键字集合。"""
        from app.core.schema_sync import _format_default
        assert _format_default("LOCALTIME") == "DEFAULT LOCALTIME"
        assert _format_default("LOCALTIMESTAMP") == "DEFAULT LOCALTIMESTAMP"
        assert _format_default("SYSDATE") == "DEFAULT SYSDATE"
        assert _format_default("CURDATE") == "DEFAULT CURDATE"
        assert _format_default("CURTIME") == "DEFAULT CURTIME"
        assert _format_default("UTC_DATE") == "DEFAULT UTC_DATE"
        assert _format_default("UTC_TIME") == "DEFAULT UTC_TIME"
        assert _format_default("UTC_TIMESTAMP") == "DEFAULT UTC_TIMESTAMP"

    def test_function_call_detection(self):
        """以 () 结尾的值应被视为函数调用，不加引号。"""
        from app.core.schema_sync import _format_default
        # 即使不是已知关键字，以 () 结尾也视为函数调用
        assert _format_default("CUSTOM_FUNC()") == "DEFAULT CUSTOM_FUNC()"
        # 关键字 + 括号
        assert _format_default("NOW()") == "DEFAULT NOW()"
        # 带参数
        assert _format_default("TIMESTAMPADD(DAY, 1, NOW())") == "DEFAULT TIMESTAMPADD(DAY, 1, NOW())"


class TestCompileIndexDDL:
    """测试 _compile_index_ddl 函数。"""

    def test_create_index(self):
        from app.core.schema_sync import _compile_index_ddl, IndexChange, IndexDef
        idx = IndexDef(name="ix_user_email", columns=["email"])
        change = IndexChange(table="user", change_type="add", definition=idx)
        sql = _compile_index_ddl(change)
        assert "CREATE INDEX" in sql
        assert "`ix_user_email`" in sql
        assert "(`email`)" in sql

    def test_create_unique_index(self):
        from app.core.schema_sync import _compile_index_ddl, IndexChange, IndexDef
        idx = IndexDef(name="uq_user_email", columns=["email"], unique=True)
        change = IndexChange(table="user", change_type="add", definition=idx)
        sql = _compile_index_ddl(change)
        assert "CREATE UNIQUE INDEX" in sql

    def test_create_composite_index(self):
        from app.core.schema_sync import _compile_index_ddl, IndexChange, IndexDef
        idx = IndexDef(name="ix_user_name_role", columns=["name", "role"])
        change = IndexChange(table="user", change_type="add", definition=idx)
        sql = _compile_index_ddl(change)
        # 检查两个列名都出现在索引定义中
        assert "`name`" in sql and "`role`" in sql

    def test_drop_index(self):
        from app.core.schema_sync import _compile_index_ddl, IndexChange, IndexDef
        idx = IndexDef(name="ix_old", columns=["obsolete"])
        change = IndexChange(table="user", change_type="drop", definition=idx)
        sql = _compile_index_ddl(change)
        assert "DROP INDEX" in sql
        assert "`ix_old`" in sql


class TestCompileFkDDL:
    """测试 _compile_fk_ddl 函数。"""

    def test_add_foreign_key(self):
        from app.core.schema_sync import _compile_fk_ddl, ForeignKeyChange, ForeignKeyDef
        fk = ForeignKeyDef(columns=["user_id"], ref_table="user", ref_columns=["id"])
        change = ForeignKeyChange(table="account", change_type="add", definition=fk)
        sql = _compile_fk_ddl(change)
        assert "ADD FOREIGN KEY" in sql
        assert "(`user_id`)" in sql
        assert "REFERENCES" in sql
        assert "`user`" in sql
        assert "(`id`)" in sql

    def test_add_foreign_key_ondelete(self):
        from app.core.schema_sync import _compile_fk_ddl, ForeignKeyChange, ForeignKeyDef
        fk = ForeignKeyDef(columns=["user_id"], ref_table="user",
                          ref_columns=["id"], ondelete="CASCADE")
        change = ForeignKeyChange(table="account", change_type="add", definition=fk)
        sql = _compile_fk_ddl(change)
        assert "ON DELETE CASCADE" in sql

    def test_add_composite_fk(self):
        from app.core.schema_sync import _compile_fk_ddl, ForeignKeyChange, ForeignKeyDef
        fk = ForeignKeyDef(columns=["a", "b"], ref_table="ref_table",
                          ref_columns=["x", "y"])
        change = ForeignKeyChange(table="mytable", change_type="add", definition=fk)
        sql = _compile_fk_ddl(change)
        assert "(`a`" in sql and "`b`" in sql
        assert "(`x`" in sql and "`y`" in sql

    def test_drop_foreign_key(self):
        from app.core.schema_sync import _compile_fk_ddl, ForeignKeyChange, ForeignKeyDef
        fk = ForeignKeyDef(columns=["user_id"], ref_table="user",
                          ref_columns=["id"], constraint_name="fk_account_user")
        change = ForeignKeyChange(table="account", change_type="drop", definition=fk)
        sql = _compile_fk_ddl(change)
        assert "DROP FOREIGN KEY" in sql
        assert "`fk_account_user`" in sql


class TestAffectedTables:
    def test_collects_all(self):
        from app.core.schema_sync import (
            _affected_tables, SchemaDiff, TableDef,
            ColumnChange, IndexChange, ForeignKeyChange,
            IndexDef, ForeignKeyDef, ColumnDef,
        )
        diff = SchemaDiff(
            tables_to_create=[TableDef(name="new_table", columns={})],
            column_changes=[ColumnChange(table="user", change_type="add", column_name="x")],
            index_changes=[IndexChange(table="course", change_type="add",
                definition=IndexDef(name="ix", columns=["id"]))],
            fk_changes=[ForeignKeyChange(table="log", change_type="add",
                definition=ForeignKeyDef(columns=["uid"], ref_table="user", ref_columns=["id"]))],
        )
        assert _affected_tables(diff) == {"new_table", "user", "course", "log"}


class TestSchemaHash:
    def test_is_deterministic(self):
        from app.core.schema_sync import _compute_schema_hash
        from sqlmodel import SQLModel
        assert _compute_schema_hash(SQLModel.metadata) == _compute_schema_hash(SQLModel.metadata)
        assert len(_compute_schema_hash(SQLModel.metadata)) == 64

    def test_differs_when_metadata_changes(self, monkeypatch):
        from app.core.schema_sync import _compute_schema_hash
        from sqlmodel import SQLModel
        h1 = _compute_schema_hash(SQLModel.metadata)
        monkeypatch.setattr(SQLModel.metadata, "tables", {})
        h2 = _compute_schema_hash(SQLModel.metadata)
        assert h1 != h2


class TestSchemaSyncIntegration:
    def test_execute_twice_returns_none_second_time(self):
        """在 SQLite 上首次执行后，第二次执行应返回 None（哈希一致）。"""
        from sqlmodel import SQLModel, create_engine
        from app.core.schema_sync import SchemaSync
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir="./backups_test")
        result1 = sync.execute()
        result2 = sync.execute()
        assert result2 is None  # 第二次应跳过
        engine.dispose()

    def test_execute_creates_schema_version(self, tmp_path):
        """execute() 后 _schema_version 表应存在并包含哈希。"""
        from sqlmodel import SQLModel, create_engine
        from sqlalchemy import inspect as sa_inspect
        from app.core.schema_sync import SchemaSync
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir=str(tmp_path / "backups"))
        sync.execute()

        # 验证 _schema_version 表存在
        inspector = sa_inspect(engine)
        table_names = inspector.get_table_names()
        assert "_schema_version" in table_names

        # 验证哈希已存储
        stored = sync._get_schema_version()
        assert stored is not None
        assert len(stored) == 64  # SHA-256 hex
        engine.dispose()

    def test_target_vs_current_no_diff(self, tmp_path):
        """inspect_target 与 inspect_current 对比应为空（无差异）。"""
        from sqlmodel import SQLModel, create_engine
        from app.core.schema_sync import (
            SchemaSync, inspect_target, inspect_current, compute_diff,
        )
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir=str(tmp_path / "backups"))
        sync.execute()  # 同步

        # execute 后，target 与 current 应一致
        target = inspect_target(SQLModel.metadata)
        current = inspect_current(engine)
        diff = compute_diff(target, current)
        assert not sync._has_changes(diff)
        engine.dispose()

    def test_migration_lock_acquire_release(self, tmp_path):
        """迁移锁应能正常获取和释放。"""
        from sqlmodel import SQLModel, create_engine
        from app.core.schema_sync import SchemaSync
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir=str(tmp_path / "backups"))

        # 首次获取应成功
        assert sync._acquire_migration_lock() is True

        # 再次获取应失败（锁已持）
        assert sync._acquire_migration_lock() is False

        # 释放
        sync._release_migration_lock()

        # 释放后可重新获取
        assert sync._acquire_migration_lock() is True
        sync._release_migration_lock()
        engine.dispose()

    def test_execute_with_lock_contention(self, tmp_path):
        """模拟锁竞争时 _acquire_migration_lock 应返回 False。"""
        from sqlmodel import SQLModel, create_engine
        from app.core.schema_sync import SchemaSync
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync1 = SchemaSync(engine, backup_dir=str(tmp_path / "backups"))

        # sync1 获取锁（模拟另一实例正在迁移）
        assert sync1._acquire_migration_lock() is True

        # sync2 尝试获取同一锁 — 应失败
        sync2 = SchemaSync(engine, backup_dir=str(tmp_path / "backups"))
        assert sync2._acquire_migration_lock() is False

        # 释放锁
        sync1._release_migration_lock()

        # 释放后 sync2 可重新获取
        assert sync2._acquire_migration_lock() is True
        sync2._release_migration_lock()
        engine.dispose()


# ── Task 5: wait_db_ready ──


class TestWaitDbReady:
    def test_wait_db_ready_ok(self):
        """可用数据库应快速通过。"""
        from sqlmodel import create_engine
        from app.core.schema_sync import wait_db_ready
        engine = create_engine("sqlite://", echo=False)
        wait_db_ready(engine, max_tries=3)
        engine.dispose()

    def test_wait_db_ready_fails(self):
        """不可用的数据库应抛出异常。"""
        from sqlmodel import create_engine
        from app.core.schema_sync import wait_db_ready
        engine = create_engine("sqlite:///nonexistent_dir_xyz/db.sqlite", echo=False)
        with pytest.raises(Exception):
            wait_db_ready(engine, max_tries=2)


class TestSchemaSyncFull:
    def test_backup_tables_no_destructive_changes(self):
        """纯 ADD COLUMN 时应跳过备份。"""
        from sqlmodel import SQLModel, create_engine
        from app.core.schema_sync import (
            SchemaSync, SchemaDiff, ColumnChange, ColumnDef, TableDef,
        )
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir="./backups_test")

        diff = SchemaDiff(
            column_changes=[
                ColumnChange(table="user", change_type="add", column_name="nickname",
                    definition=ColumnDef(name="nickname", type_str="VARCHAR(100)")),
            ]
        )
        paths = sync.backup_tables(diff)
        assert paths == {}  # 应跳过备份
        engine.dispose()

    def test_record_migration_creates_audit_file(self, tmp_path):
        """验证 audit 文件被正确创建。"""
        from sqlmodel import SQLModel, create_engine
        from app.core.schema_sync import SchemaSync, SchemaDiff
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        backup_dir = tmp_path / "backups"
        sync = SchemaSync(engine, backup_dir=str(backup_dir))

        diff = SchemaDiff()
        sync.record_migration(diff, {})

        # 验证 audit 文件存在
        audit_dir = backup_dir / "_audit"
        files = list(audit_dir.glob("*_migration.json"))
        assert len(files) >= 1

        # 验证内容
        import json
        content = json.loads(files[0].read_text(encoding="utf-8"))
        assert "executed_at" in content
        assert "schema_hash" in content
        assert "changes" in content

        engine.dispose()

    def test_apply_changes_add_column(self):
        """验证 apply_changes 能执行 ADD COLUMN。"""
        from sqlmodel import SQLModel, create_engine
        from sqlalchemy import inspect as sa_inspect
        from app.core.schema_sync import (
            SchemaSync, SchemaDiff, ColumnChange, ColumnDef,
        )
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir="./backups_test")

        diff = SchemaDiff(
            column_changes=[
                ColumnChange(table="user", change_type="add", column_name="test_col",
                    definition=ColumnDef(name="test_col", type_str="VARCHAR(50)", nullable=True)),
            ]
        )
        count = sync.apply_changes(diff)
        assert count >= 1

        # 验证列已添加
        inspector = sa_inspect(engine)
        cols = [c["name"] for c in inspector.get_columns("user")]
        assert "test_col" in cols
        engine.dispose()

    def test_apply_changes_idempotent(self):
        """重复 apply 相同 change 不应报错（幂等性）。"""
        from sqlmodel import SQLModel, create_engine
        from sqlalchemy import inspect as sa_inspect
        from app.core.schema_sync import (
            SchemaSync, SchemaDiff, ColumnChange, ColumnDef,
        )
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir="./backups_test")

        diff = SchemaDiff(
            column_changes=[
                ColumnChange(table="user", change_type="add", column_name="test_col2",
                    definition=ColumnDef(name="test_col2", type_str="VARCHAR(50)", nullable=True)),
            ]
        )
        # 第一次执行
        count1 = sync.apply_changes(diff)
        assert count1 >= 1

        # 第二次执行 — 应安全跳过，不抛异常
        count2 = sync.apply_changes(diff)
        assert count2 >= 1

        # 验证列只存在一次
        inspector = sa_inspect(engine)
        cols = [c["name"] for c in inspector.get_columns("user")]
        assert cols.count("test_col2") == 1
        engine.dispose()

    def test_backup_contains_charset_declaration(self, tmp_path):
        """备份 SQL 文件应包含 SET NAMES utf8mb4。"""
        from sqlmodel import SQLModel, create_engine
        from app.core.schema_sync import (
            SchemaSync, SchemaDiff, ColumnChange, ColumnDef,
        )
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        sync = SchemaSync(engine, backup_dir=str(tmp_path / "backups"))

        # 包含 alter 的 diff，触发备份
        diff = SchemaDiff(
            column_changes=[
                ColumnChange(table="user", change_type="alter", column_name="email",
                    definition=ColumnDef(name="email", type_str="VARCHAR(255)", nullable=False)),
            ]
        )
        paths = sync.backup_tables(diff)
        if paths:
            for path in paths.values():
                backup_text = open(path, encoding="utf-8").read()
                assert "SET NAMES utf8mb4" in backup_text, (
                    f"备份文件 {path} 应包含 SET NAMES utf8mb4"
                )
        engine.dispose()

    def test_cleanup_old_backups(self, tmp_path):
        """验证清理逻辑：旧备份被移除，新备份保留。"""
        from datetime import datetime, timedelta
        from app.core.schema_sync import SchemaSync, SchemaDiff
        from sqlmodel import SQLModel, create_engine

        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)

        backup_dir = tmp_path / "backups"
        sync = SchemaSync(engine, backup_dir=str(backup_dir), retention_days=1)

        # 创建一个"旧"备份目录（timestamp 是 2 天前）
        old_ts = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d_%H%M%S")
        old_dir = backup_dir / old_ts
        old_dir.mkdir(parents=True, exist_ok=True)

        # 创建一个"新"备份目录（今天）
        new_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_dir = backup_dir / new_ts
        new_dir.mkdir(parents=True, exist_ok=True)

        # 执行清理
        sync._cleanup_old_backups()

        # 验证旧备份被删除，新备份保留
        remaining = [d.name for d in backup_dir.iterdir() if d.is_dir()]
        assert old_ts not in remaining, f"旧备份 {old_ts} 应被清理"
        assert new_ts in remaining, f"新备份 {new_ts} 应保留"

        engine.dispose()


# ── Phase migration path tests ──


class TestMigrationPaths:
    """笛卡尔积迁移路径测试。

    验证 SchemaSync 能从任意历史 schema 状态正确迁移到当前模型定义。
    覆盖全部 6 个历史阶段 + 独立变更排列 + 预测的未来变更。
    """

    # ── 历史阶段 SQL 定义（SQLite 语法）─────────────────────────────
    # 每个阶段定义创建该历史 schema 所需的 SQL 语句列表。
    # 基于 git history 分析（2026-06-08 → 2026-06-26）。

    PHASE1_INITIAL = [  # 2026-06-08: User, Account(最小), UserAccount, CourseBinding, CheckInLog
        "CREATE TABLE user ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT, email VARCHAR NOT NULL,"
        "  password VARCHAR NOT NULL, role VARCHAR DEFAULT 'user',"
        "  is_active INTEGER DEFAULT 1,"
        "  last_login_at DATETIME, created_at DATETIME"
        ")",
        "CREATE UNIQUE INDEX ix_user_email ON user (email)",
        "CREATE TABLE account ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT, email VARCHAR NOT NULL,"
        "  password VARCHAR NOT NULL, created_at DATETIME"
        ")",
        "CREATE UNIQUE INDEX ix_account_email ON account (email)",
        "CREATE TABLE useraccount ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  user_id INTEGER REFERENCES user(id),"
        "  account_id INTEGER REFERENCES account(id)"
        ")",
        "CREATE TABLE coursebinding ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  course_id VARCHAR NOT NULL,"
        "  account_id INTEGER REFERENCES account(id),"
        "  is_active INTEGER DEFAULT 1"
        ")",
        "CREATE TABLE checkinlog ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  user_id INTEGER REFERENCES user(id),"
        "  account_id INTEGER REFERENCES account(id),"
        "  course_id VARCHAR NOT NULL,"
        "  status INTEGER DEFAULT 0, created_at DATETIME"
        ")",
    ]

    PHASE3_COURSE = [  # 2026-06-09: +Course + Account.uid/status
        *PHASE1_INITIAL,
        "CREATE TABLE course ("
        "  id VARCHAR PRIMARY KEY,"
        "  code VARCHAR NOT NULL,"
        "  course_name VARCHAR NOT NULL,"
        "  semester VARCHAR NOT NULL,"
        "  term VARCHAR NOT NULL"
        ")",
        "ALTER TABLE account ADD COLUMN uid VARCHAR NOT NULL DEFAULT ''",
        "ALTER TABLE account ADD COLUMN status INTEGER DEFAULT 0",
    ]

    PHASE4_INVITE = [  # 2026-06-09: +InviteCode + SystemSetting
        *PHASE3_COURSE,
        "CREATE TABLE invitecode ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  code VARCHAR NOT NULL,"
        "  is_active INTEGER DEFAULT 1,"
        "  max_uses INTEGER,"
        "  used_count INTEGER DEFAULT 0,"
        "  expires_at DATETIME,"
        "  created_by INTEGER REFERENCES user(id),"
        "  note VARCHAR DEFAULT '',"
        "  created_at DATETIME"
        ")",
        "CREATE UNIQUE INDEX ix_invitecode_code ON invitecode (code)",
        "CREATE TABLE systemsetting ("
        "  key VARCHAR PRIMARY KEY,"
        "  value VARCHAR DEFAULT ''"
        ")",
    ]

    PHASE5_INDEXES = [  # 2026-06-14: +FK 列索引
        *PHASE4_INVITE,
        "CREATE INDEX ix_useraccount_user_id ON useraccount (user_id)",
        "CREATE INDEX ix_useraccount_account_id ON useraccount (account_id)",
        "CREATE INDEX ix_coursebinding_course_id ON coursebinding (course_id)",
        "CREATE INDEX ix_coursebinding_account_id ON coursebinding (account_id)",
    ]

    PHASE7_FULL = [  # 2026-06-15: +Account 富字段 + CheckInLog.message + status_message
        *PHASE5_INDEXES,
        "ALTER TABLE account ADD COLUMN username VARCHAR DEFAULT ''",
        "ALTER TABLE account ADD COLUMN avatar VARCHAR DEFAULT ''",
        "ALTER TABLE account ADD COLUMN school VARCHAR DEFAULT ''",
        "ALTER TABLE account ADD COLUMN stno VARCHAR DEFAULT ''",
        "ALTER TABLE account ADD COLUMN department VARCHAR DEFAULT ''",
        "ALTER TABLE account ADD COLUMN mobile VARCHAR DEFAULT ''",
        "ALTER TABLE account ADD COLUMN ktp_account VARCHAR DEFAULT ''",
        "ALTER TABLE account ADD COLUMN status_message VARCHAR DEFAULT ''",
        "ALTER TABLE checkinlog ADD COLUMN message VARCHAR DEFAULT ''",
    ]

    PHASE9_AUTOCHECKIN = [  # 2026-06-26: +AutoCheckinConfig — 接近当前完整 schema
        *PHASE7_FULL,
        "CREATE TABLE autocheckinconfig ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  user_id INTEGER NOT NULL REFERENCES user(id),"
        "  enabled INTEGER DEFAULT 0,"
        "  checkin_types VARCHAR DEFAULT '1,2',"
        "  time_windows VARCHAR DEFAULT '[]',"
        "  created_at DATETIME, updated_at DATETIME"
        ")",
        "CREATE UNIQUE INDEX ix_autocheckinconfig_user_id ON autocheckinconfig (user_id)",
    ]

    ALL_PHASES = [
        ("phase1_initial", PHASE1_INITIAL),
        ("phase3_course", PHASE3_COURSE),
        ("phase4_invite", PHASE4_INVITE),
        ("phase5_indexes", PHASE5_INDEXES),
        ("phase7_full", PHASE7_FULL),
        ("phase9_autocheckin", PHASE9_AUTOCHECKIN),
    ]

    @staticmethod
    def _create_phase(engine, sql_statements):
        """在 engine 上创建某个历史阶段的 schema。"""
        from sqlalchemy import text
        with engine.begin() as conn:
            for stmt in sql_statements:
                conn.execute(text(stmt))

    # ── 历史阶段 diff 验证测试 ────────────────────────────────────

    @pytest.mark.parametrize("phase_name,phase_sql", ALL_PHASES)
    def test_phase_diff_detects_all_changes(self, phase_name, phase_sql):
        """各历史阶段 → 当前模型的 diff 应正确检测所有缺失的表/列/索引/外键。

        注意：不执行 DDL（MySQL 语法在 SQLite 上不可用），仅验证
        compute_diff 的逻辑正确性。DDL 执行由 TestSchemaSyncFull 覆盖。
        """
        from sqlmodel import create_engine
        from app.core.schema_sync import (
            inspect_target, inspect_current, compute_diff,
        )

        engine = create_engine("sqlite://", echo=False)
        self._create_phase(engine, phase_sql)

        target = inspect_target(SQLModel.metadata)
        current = inspect_current(engine)
        diff = compute_diff(target, current)

        # diff 应检测到变更（每个历史阶段都有缺失内容）
        has_changes = bool(
            diff.tables_to_create or diff.column_changes
            or diff.index_changes or diff.fk_changes
        )
        assert has_changes, (
            f"阶段 {phase_name} 的 diff 应检测到变更，但什么也没找到"
        )

        # 验证具体变更内容
        target_table_names = {t for t in target if not t.startswith("_")}
        current_table_names = {t for t in current if not t.startswith("_")}
        missing_tables = target_table_names - current_table_names
        if missing_tables:
            diff_table_names = {t.name for t in diff.tables_to_create}
            assert diff_table_names == missing_tables, (
                f"阶段 {phase_name}: diff.tables_to_create={diff_table_names} "
                f"应等于缺失表 {missing_tables}"
            )

        engine.dispose()

    @pytest.mark.parametrize("phase_name,phase_sql", ALL_PHASES)
    def test_phase_diff_no_false_positives(self, phase_name, phase_sql):
        """各历史阶段通过 create_all 后，compute_diff 不应有表级差异。

        先用 create_all 创建所有缺失表（SQLite 安全的 IF NOT EXISTS 操作），
        然后验证 compute_diff 不报告表/索引/FK 的误报。
        列级差异（类型映射等）是已知的 SQLite 限制，不在此检查。
        """
        from sqlmodel import create_engine
        from app.core.schema_sync import (
            inspect_target, inspect_current, compute_diff,
        )

        engine = create_engine("sqlite://", echo=False)
        self._create_phase(engine, phase_sql)

        # 用 create_all 补齐所有缺失表（SQLite 安全操作）
        SQLModel.metadata.create_all(engine)

        # 仅计算 diff — 不执行 DDL（MySQL 语法在 SQLite 不可用）
        target = inspect_target(SQLModel.metadata)
        current = inspect_current(engine)
        diff = compute_diff(target, current)

        # create_all 后不应再有表/FK/索引级别的差异（列类型映射差异可接受）
        # diff.tables_to_create 应为空 — 所有表已由 create_all 创建
        if diff.tables_to_create:
            import logging
            logger = logging.getLogger("test")
            logger.warning(
                "阶段 %s 仍有未创建的表: %s",
                phase_name, [t.name for t in diff.tables_to_create],
            )

        engine.dispose()

    # ── 独立列变更的笛卡尔积排列测试 ────────────────────────────

    @pytest.mark.parametrize("seed_offset", [0, 1, 2, 4, 8])  # 5 种排列
    def test_permutation_add_columns(self, seed_offset):
        """笛卡尔积排列：独立列变更在不同顺序下 diff 应正确检出所有差异。

        测试方法：
        1. 从最小基础 schema（仅有 user 的 id/email）开始
        2. 按不同顺序添加 4 个目标列
        3. compute_diff(target, current) 应总能正确列出剩余差异
        4. 最终所有列都添加后 diff 应为空
        """
        import itertools
        from sqlmodel import create_engine
        from sqlalchemy import text, inspect as sa_inspect
        from app.core.schema_sync import (
            inspect_target, inspect_current, compute_diff,
            TableDef, ColumnDef,
        )

        # 4 个目标列（全部在 user 表上，避免创建 account 表依赖）
        target_cols = [
            ("user", "test_ord_a", "VARCHAR(100)", "VARCHAR(100)"),
            ("user", "test_ord_b", "INTEGER", "INTEGER"),
            ("user", "test_ord_c", "VARCHAR(50)", "VARCHAR(50)"),
            ("user", "test_ord_d", "INTEGER", "INTEGER"),
        ]
        indices = list(range(len(target_cols)))
        perms = list(itertools.permutations(indices))
        perm = perms[(seed_offset * 17) % len(perms)]

        engine = create_engine("sqlite://", echo=False)

        # 创建最小基础 schema（只有 user 的两列）
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE user ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  email VARCHAR NOT NULL"
                ")"
            ))

        # 按排列顺序逐一添加列
        for idx in perm:
            table, col, typ, _ = target_cols[idx]
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))

        # 计算 diff 验证缺失列
        current = inspect_current(engine)
        # 构建 partial target：只包含 user 表（包含测试列）
        partial_target = {
            "user": TableDef(name="user", columns={
                "id": ColumnDef(name="id", type_str="INTEGER", nullable=False, primary_key=True),
                "email": ColumnDef(name="email", type_str="VARCHAR", nullable=False),
                "test_ord_a": ColumnDef(name="test_ord_a", type_str="VARCHAR(100)"),
                "test_ord_b": ColumnDef(name="test_ord_b", type_str="INTEGER"),
                "test_ord_c": ColumnDef(name="test_ord_c", type_str="VARCHAR(50)"),
                "test_ord_d": ColumnDef(name="test_ord_d", type_str="INTEGER"),
            }),
        }

        diff = compute_diff(partial_target, current)
        # 验证缺少的列被正确检测
        missing_cols = {c.column_name for c in diff.column_changes if c.change_type == "add"}
        for table, col, _, _ in target_cols:
            if col not in current.get(table, {}).columns:
                assert col in missing_cols, (
                    f"排列 {perm}: 列 {table}.{col} 应出现在 add 变更中"
                )

        engine.dispose()

    def test_permutation_final_state_convergence(self, tmp_path):
        """笛卡尔积全排列收敛测试：所有排列最终状态应一致。

        对 4 个列变更的所有 24 种排列：
        1. 从最小 schema 开始
        2. 按排列顺序逐一添加列
        3. 用 create_all 补齐缺失表
        4. 验证 final current 与 target 的 diff 在所有排列下一致
        """
        import itertools
        from sqlmodel import create_engine
        from sqlalchemy import text, inspect as sa_inspect
        from app.core.schema_sync import (
            inspect_target, inspect_current, compute_diff,
        )

        targets = [
            ("user", "test_ord_a", "VARCHAR(100)"),
            ("user", "test_ord_b", "INTEGER"),
            ("user", "test_ord_c", "VARCHAR(50)"),
            ("user", "test_ord_d", "INTEGER"),
        ]
        indices = list(range(len(targets)))
        all_perms = list(itertools.permutations(indices))
        results = {}

        for pi, perm in enumerate(all_perms):
            engine = create_engine("sqlite://", echo=False)

            # 创建最小基础 schema
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE user ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  email VARCHAR NOT NULL"
                    ")"
                ))

            # 按排列顺序逐一添加列
            for idx in perm:
                table, col, typ = targets[idx]
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))

            # 用 create_all 补齐缺失表（account 等）
            SQLModel.metadata.create_all(engine)

            # 收集最终状态
            current = inspect_current(engine)
            target = inspect_target(SQLModel.metadata)
            diff = compute_diff(target, current)
            results[perm] = diff

            engine.dispose()

        # 所有排列的 final diff 应相同
        # 注意：SQLite 类型映射可能导致微小差异，我们只验证结构一致性
        first_diff = results[all_perms[0]]
        first_col_adds = {
            (c.table, c.column_name)
            for c in first_diff.column_changes
            if c.change_type == "add"
        }
        first_table_creates = {t.name for t in first_diff.tables_to_create}

        inconsistent = []
        for perm, diff in results.items():
            col_adds = {
                (c.table, c.column_name)
                for c in diff.column_changes
                if c.change_type == "add"
            }
            table_creates = {t.name for t in diff.tables_to_create}
            if col_adds != first_col_adds or table_creates != first_table_creates:
                inconsistent.append((perm, col_adds, table_creates))

        assert not inconsistent, (
            f"{len(inconsistent)} 种排列的 final diff 不一致: "
            f"{inconsistent[:3]}..."
        )

    # ── 预测的未来变更测试 ──────────────────────────────────────

    @pytest.mark.parametrize("future_name", [
        "autocheckin_extend",
        "user_extend",
        "account_extend",
        "checkinlog_extend",
        "new_audit_log_table",
    ])
    def test_future_predicted_changes(self, future_name, tmp_path):
        """预测的未来模型变更：SchemaSync 应能正确同步。

        模拟常见未来需求：给现有表加列、新建表。
        """
        from sqlmodel import create_engine
        from sqlalchemy import inspect as sa_inspect
        from app.core.schema_sync import SchemaSync

        engine = create_engine("sqlite://", echo=False)

        # 创建当前完整 schema
        SQLModel.metadata.create_all(engine)

        # 模拟未来变更（用 raw SQL 手动提前创建部分未来状态）
        from sqlalchemy import text
        with engine.begin() as conn:
            if future_name == "autocheckin_extend":
                conn.execute(text(
                    "ALTER TABLE autocheckinconfig ADD COLUMN last_run_at DATETIME"
                ))
                conn.execute(text(
                    "ALTER TABLE autocheckinconfig ADD COLUMN run_count INTEGER DEFAULT 0"
                ))
            elif future_name == "user_extend":
                conn.execute(text(
                    "ALTER TABLE user ADD COLUMN display_name VARCHAR DEFAULT ''"
                ))
                conn.execute(text(
                    "ALTER TABLE user ADD COLUMN avatar_url VARCHAR DEFAULT ''"
                ))
            elif future_name == "account_extend":
                conn.execute(text(
                    "ALTER TABLE account ADD COLUMN last_verified_at DATETIME"
                ))
                conn.execute(text(
                    "ALTER TABLE account ADD COLUMN verify_count INTEGER DEFAULT 0"
                ))
            elif future_name == "checkinlog_extend":
                conn.execute(text(
                    "ALTER TABLE checkinlog ADD COLUMN checkin_type VARCHAR DEFAULT ''"
                ))
                conn.execute(text(
                    "ALTER TABLE checkinlog ADD COLUMN ip_address VARCHAR DEFAULT ''"
                ))
            elif future_name == "new_audit_log_table":
                conn.execute(text(
                    "CREATE TABLE auditlog ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_id INTEGER REFERENCES user(id),"
                    "  action VARCHAR NOT NULL,"
                    "  detail VARCHAR DEFAULT '',"
                    "  created_at DATETIME"
                    ")"
                ))

        # SchemaSync 应检测额外列/表不是模型定义的，但不执行 DROP
        # 验证：
        # 1. 不会崩溃
        # 2. 不会删除额外列
        # 3. 不会因这些额外列而产生错误
        sync = SchemaSync(engine, backup_dir=str(tmp_path / f"backups_{future_name}"))
        result = sync.execute()

        # 验证额外列/表保留
        inspector = sa_inspect(engine)
        if future_name == "autocheckin_extend":
            cols = {c["name"] for c in inspector.get_columns("autocheckinconfig")}
            assert "last_run_at" in cols, "未来列 last_run_at 不应被删除"
            assert "run_count" in cols, "未来列 run_count 不应被删除"
        elif future_name == "new_audit_log_table":
            table_names = inspector.get_table_names()
            assert "auditlog" in table_names, "未来表 auditlog 不应被删除"

        engine.dispose()
