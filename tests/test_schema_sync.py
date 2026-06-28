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
        assert "ALTER TABLE user" in sql
        assert "ADD COLUMN" in sql
        assert "VARCHAR(100)" in sql

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
        assert "nick_name" in sql
        assert "nickname" in sql

    def test_alter_column_type(self):
        from app.core.schema_sync import _compile_ddl, ColumnChange, ColumnDef
        change = ColumnChange(table="user", change_type="alter", column_name="name",
            definition=ColumnDef(name="name", type_str="VARCHAR(100)", nullable=False))
        sql = _compile_ddl(change)
        assert "MODIFY COLUMN" in sql
        assert "VARCHAR(100)" in sql

    def test_drop_raises(self):
        import pytest
        from app.core.schema_sync import _compile_ddl, ColumnChange
        change = ColumnChange(table="user", change_type="drop", column_name="old_col")
        with pytest.raises(NotImplementedError):
            _compile_ddl(change)


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
