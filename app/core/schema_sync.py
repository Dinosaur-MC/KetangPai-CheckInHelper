"""Fully automatic schema synchronization — replaces _migrate()."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import tenacity
from sqlalchemy import Engine, inspect as sa_inspect, text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel, Session, select

logger = logging.getLogger(__name__)


@dataclass
class ColumnDef:
    name: str
    type_str: str          # 规范化类型名，如 "VARCHAR(255)"
    nullable: bool = True
    default: str | None = None
    autoincrement: bool = False
    primary_key: bool = False
    comment: str = ""


@dataclass
class IndexDef:
    name: str
    columns: list[str]
    unique: bool = False


@dataclass
class ForeignKeyDef:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    ondelete: str = ""
    constraint_name: str = ""


@dataclass
class TableDef:
    name: str
    columns: dict[str, ColumnDef]
    indexes: list[IndexDef] = field(default_factory=list)
    foreign_keys: list[ForeignKeyDef] = field(default_factory=list)


# ── Diff result types ──

@dataclass
class ColumnChange:
    table: str
    change_type: Literal["add", "drop", "alter", "rename"]
    column_name: str
    old_name: str | None = None
    definition: ColumnDef | None = None


@dataclass
class IndexChange:
    table: str
    change_type: Literal["add", "drop"]
    definition: IndexDef


@dataclass
class ForeignKeyChange:
    table: str
    change_type: Literal["add", "drop"]
    definition: ForeignKeyDef


@dataclass
class SchemaDiff:
    tables_to_create: list[TableDef] = field(default_factory=list)
    column_changes: list[ColumnChange] = field(default_factory=list)
    index_changes: list[IndexChange] = field(default_factory=list)
    fk_changes: list[ForeignKeyChange] = field(default_factory=list)


def levenshtein_distance(s1: str, s2: str) -> int:
    """计算编辑距离。"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _type_family(type_str: str) -> str:
    """获取类型的家族类别，忽略长度/精度参数。"""
    base = type_str.split("(")[0].upper()
    if base in ("VARCHAR", "CHAR", "TEXT", "LONGTEXT", "MEDIUMTEXT",
                 "TINYTEXT", "NVARCHAR", "NCHAR", "CLOB"):
        return "string"
    if base in ("INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT", "SERIAL", "INT4", "INT8"):
        return "integer"
    if base in ("FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC", "NUMBER"):
        return "numeric"
    if base == "BOOLEAN":
        return "boolean"
    if base in ("DATETIME", "TIMESTAMP", "DATE", "TIME"):
        return "datetime"
    return base.lower()


def _detect_rename(
    deleted_names: set[str],
    added_names: set[str],
    current_types: dict[str, str],
    target_types: dict[str, str],
    max_distance: int = 2,
    fk_column_names: set[str] | None = None,
) -> list[tuple[str, str]]:
    """检测可能的列改名对。

    安全策略：
    - 类型族不匹配 → 不猜测
    - 编辑距离 > max_distance → 不猜测
    - 外键列 → 不猜测（重命名 FK 列风险极高）
    - 所有检测到的重命名输出 INFO 日志

    Args:
        deleted_names: current 中有的、target 中没有的列名集合
        added_names: target 中有的、current 中没有的列名集合
        current_types: current 中的 {col_name: type_str}
        target_types: target 中的 {col_name: type_str}
        max_distance: 最大编辑距离（默认 2，原为 3）
        fk_column_names: 外键列名集合，这些列不参与重命名猜测

    Returns:
        [(old_name, new_name), ...] 列表
    """
    if fk_column_names is None:
        fk_column_names = set()

    results: list[tuple[str, str, int]] = []
    for old_name in sorted(deleted_names):
        # 跳过外键列 — 不猜测 FK 列的重命名
        if old_name in fk_column_names:
            logger.debug("列 %s 是外键列，跳过重命名猜测", old_name)
            continue

        old_type = current_types.get(old_name, "")
        old_family = _type_family(old_type)
        best_match: str | None = None
        best_dist = max_distance + 1
        for new_name in sorted(added_names):
            new_type = target_types.get(new_name, "")
            new_family = _type_family(new_type)
            if old_family != new_family:
                continue
            dist = levenshtein_distance(old_name, new_name)
            if dist < best_dist:
                best_dist = dist
                best_match = new_name
        if best_match is not None:
            results.append((old_name, best_match, best_dist))
    results.sort(key=lambda x: x[2])
    used_new: set[str] = set()
    final: list[tuple[str, str]] = []
    for old_name, new_name, dist in results:
        if new_name not in used_new:
            final.append((old_name, new_name))
            used_new.add(new_name)
            logger.info("检测到列改名: %s → %s (编辑距离 %d)", old_name, new_name, dist)
    return final


def compute_diff(target: dict[str, TableDef], current: dict[str, TableDef]) -> SchemaDiff:
    """对比 target（模型）与 current（数据库），生成变更列表。"""
    diff = SchemaDiff()

    # 1. 新增表
    for name, tdef in target.items():
        if name not in current:
            diff.tables_to_create.append(tdef)

    # 2. 逐表比对列
    for name, tdef in target.items():
        if name not in current:
            continue
        cur = current[name]

        target_col_names = set(tdef.columns.keys())
        current_col_names = set(cur.columns.keys())
        deleted = current_col_names - target_col_names
        added = target_col_names - current_col_names
        common = current_col_names & target_col_names

        current_types = {n: cur.columns[n].type_str for n in deleted}
        target_types = {n: tdef.columns[n].type_str for n in added}

        # 收集当前表中的外键列名 — 这些列不参与重命名猜测
        fk_column_names: set[str] = set()
        for fk in cur.foreign_keys:
            fk_column_names.update(fk.columns)
        renames = _detect_rename(deleted, added, current_types, target_types,
                                 fk_column_names=fk_column_names)
        renamed_old = {r[0] for r in renames}
        renamed_new = {r[1] for r in renames}

        for old_name, new_name in renames:
            diff.column_changes.append(ColumnChange(
                table=name, change_type="rename",
                column_name=new_name, old_name=old_name,
                definition=tdef.columns[new_name],
            ))

        truly_deleted = deleted - renamed_old
        if truly_deleted:
            logger.warning(
                "表 %s 中的列 %s 在模型中不存在，将保持不动",
                name, sorted(truly_deleted),
            )

        truly_added = added - renamed_new
        for col_name in sorted(truly_added):
            diff.column_changes.append(ColumnChange(
                table=name, change_type="add",
                column_name=col_name,
                definition=tdef.columns[col_name],
            ))

        for col_name in sorted(common):
            tc = tdef.columns[col_name]
            cc = cur.columns[col_name]
            if (tc.type_str != cc.type_str
                    or tc.nullable != cc.nullable
                    or tc.default != cc.default):
                diff.column_changes.append(ColumnChange(
                    table=name, change_type="alter",
                    column_name=col_name, definition=tc,
                ))

        # 索引对比 — 跳过外键列的自动索引（MySQL 自动为 FK 建索引，模型不声明）
        _fk_col_sets = {frozenset(fk.columns) for fk in cur.foreign_keys}
        current_idx_map = {(idx.name, tuple(idx.columns)): idx for idx in cur.indexes}
        target_idx_map = {(idx.name, tuple(idx.columns)): idx for idx in tdef.indexes}
        for key, idx in target_idx_map.items():
            if key not in current_idx_map:
                diff.index_changes.append(IndexChange(
                    table=name, change_type="add", definition=idx,
                ))
        for key, idx in current_idx_map.items():
            if key not in target_idx_map:
                if frozenset(idx.columns) in _fk_col_sets:
                    logger.debug("跳过外键索引 %s.%s", name, idx.name)
                    continue
                diff.index_changes.append(IndexChange(
                    table=name, change_type="drop", definition=idx,
                ))

        # 外键对比
        current_fk_map = {}
        for fk in cur.foreign_keys:
            key = (tuple(fk.columns), fk.ref_table, tuple(fk.ref_columns))
            current_fk_map[key] = fk
        target_fk_map = {}
        for fk in tdef.foreign_keys:
            key = (tuple(fk.columns), fk.ref_table, tuple(fk.ref_columns))
            target_fk_map[key] = fk
        for key, fk in target_fk_map.items():
            if key not in current_fk_map:
                diff.fk_changes.append(ForeignKeyChange(
                    table=name, change_type="add", definition=fk,
                ))
        for key, fk in current_fk_map.items():
            if key not in target_fk_map:
                diff.fk_changes.append(ForeignKeyChange(
                    table=name, change_type="drop", definition=fk,
                ))

    return diff


def _extract_default(default_val) -> str | None:
    """提取列默认值的可读文本形式。"""
    if default_val is None:
        return None
    if isinstance(default_val, str):
        if len(default_val) >= 2 and default_val[0] == "'" and default_val[-1] == "'":
            return default_val[1:-1]
        return default_val
    return str(default_val)


def _type_to_string(col_type) -> str:
    """将 SQLAlchemy 类型规范化为统一字符串表示。

    MySQL 要求 VARCHAR 必须有长度，AutoString 默认无长度 → 补为 VARCHAR(255)。
    其他类型直接返回大写形式。
    """
    raw = str(col_type)
    upper = raw.upper()
    # VARCHAR 无长度时补默认值（MySQL 必要）
    if upper == "VARCHAR":
        return "VARCHAR(255)"
    return upper


def inspect_target(metadata) -> dict[str, TableDef]:
    """从 SQLModel metadata 提取所有表的 target 定义。"""
    result: dict[str, TableDef] = {}
    for table_name, table in sorted(metadata.tables.items()):
        if table_name.startswith("_"):
            continue  # 跳过内部表（如 _schema_version）

        columns: dict[str, ColumnDef] = {}
        for col in table.columns:
            default_val = None
            if col.server_default is not None:
                arg = col.server_default.arg
                default_val = arg.text if hasattr(arg, 'text') else str(arg)
            columns[col.name] = ColumnDef(
                name=col.name,
                type_str=_type_to_string(col.type),
                nullable=col.nullable,
                default=default_val,
                autoincrement=col.autoincrement is True or (
                    col.autoincrement == "auto" and col.primary_key
                    and _type_to_string(col.type) in ("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "INT")
                ),
                primary_key=col.primary_key,
            )

        indexes: list[IndexDef] = []
        for idx in table.indexes:
            indexes.append(IndexDef(
                name=idx.name or f"ix_{table_name}_{'_'.join(idx.columns.keys())}",
                columns=list(idx.columns.keys()),
                unique=idx.unique,
            ))

        fks: list[ForeignKeyDef] = []
        for fk in table.foreign_key_constraints:
            cols = list(fk.columns.keys())
            if fk.elements:
                fks.append(ForeignKeyDef(
                    columns=cols,
                    ref_table=list(fk.elements)[0].column.table.name,
                    ref_columns=[str(elem.column.name) for elem in fk.elements],
                    ondelete=fk.ondelete or "",
                    constraint_name=fk.name or "",
                ))

        result[table_name] = TableDef(name=table_name, columns=columns, indexes=indexes, foreign_keys=fks)
    return result


def inspect_current(engine: Engine) -> dict[str, TableDef]:
    """使用 SQLAlchemy 反射获取数据库当前 schema。"""
    inspector = sa_inspect(engine)
    result: dict[str, TableDef] = {}
    for table_name in inspector.get_table_names():
        if table_name.startswith("_"):
            continue

        columns: dict[str, ColumnDef] = {}
        for col in inspector.get_columns(table_name):
            columns[col["name"]] = ColumnDef(
                name=col["name"],
                type_str=_type_to_string(col["type"]),
                nullable=col.get("nullable", True),
                default=_extract_default(col.get("default")),
                autoincrement=col.get("autoincrement", False),
                primary_key=col.get("primary_key", False),
            )

        indexes: list[IndexDef] = []
        for idx in inspector.get_indexes(table_name):
            indexes.append(IndexDef(
                name=idx["name"] or f"ix_{table_name}_{'_'.join(idx['column_names'])}",
                columns=list(idx["column_names"]),
                unique=idx.get("unique", False),
            ))

        fks: list[ForeignKeyDef] = []
        for fk in inspector.get_foreign_keys(table_name):
            fks.append(ForeignKeyDef(
                columns=list(fk["constrained_columns"]),
                ref_table=fk["referred_table"],
                ref_columns=list(fk["referred_columns"]),
                ondelete=fk.get("options", {}).get("ondelete", ""),
                constraint_name=fk.get("name", "") or "",
            ))

        result[table_name] = TableDef(name=table_name, columns=columns, indexes=indexes, foreign_keys=fks)
    return result


# ── DDL 编译 ──


def _format_default(default_val: str) -> str:
    """将默认值格式化为 SQL 片段。数字和 SQL 关键字不加引号。

    三种情况：
    1. SQL 函数/关键字（如 CURRENT_TIMESTAMP, NOW()）→ 不加引号
    2. 数字（整数、浮点数）→ 不加引号
    3. 字符串 → 加单引号并转义内部单引号
    """
    upper = default_val.upper()

    # 1. SQL 函数（以 () 结尾）
    if "()" in upper:
        return f"DEFAULT {default_val}"

    # 2. 已知 SQL 关键字
    sql_keywords = {
        "CURRENT_TIMESTAMP", "NOW", "CURRENT_DATE", "CURRENT_TIME",
        "LOCALTIME", "LOCALTIMESTAMP", "SYSDATE",
        "CURDATE", "CURTIME",
        "UTC_DATE", "UTC_TIME", "UTC_TIMESTAMP",
        "TRUE", "FALSE", "NULL",
    }
    if upper in sql_keywords:
        return f"DEFAULT {default_val}"

    # 3. 数字（整数、浮点数，包括负数）
    try:
        float(default_val)
        return f"DEFAULT {default_val}"
    except ValueError:
        pass

    # 4. 字符串 — 需要引号并转义内部的单引号
    escaped = default_val.replace("'", "\\'")
    return f"DEFAULT '{escaped}'"


def _qt(ident: str) -> str:
    """用反引号包装标识符（表名/列名/索引名），防止 MySQL 保留字冲突。"""
    return f"`{ident}`"


def _qteach(items: list[str]) -> str:
    """用反引号包装每个标识符并以逗号连接。"""
    return ", ".join(_qt(i) for i in items)


def _is_integer_type(type_str: str) -> bool:
    """检查类型是否为整数类型（可支持 AUTO_INCREMENT）。"""
    base = type_str.split("(")[0].upper()
    return base in ("INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT")


def _compile_ddl(change: ColumnChange) -> str:
    """将 ColumnChange 编译为 MySQL DDL 语句。"""
    table = _qt(change.table)
    if change.change_type == "add":
        col = change.definition
        assert col is not None
        if not col.nullable and col.default is None:
            logger.warning(
                "向表 %s 添加 NOT NULL 列 %s 无 DEFAULT 值。"
                " 若表中有数据，MySQL 将拒绝此操作。推荐先添加 NULLABLE 列，"
                " 填充数据后再设 NOT NULL，或指定 DEFAULT 值。",
                change.table, col.name,
            )
        parts = [f"ALTER TABLE {table} ADD COLUMN {_qt(col.name)} {col.type_str}"]
        if not col.nullable:
            parts.append("NOT NULL")
        if col.default is not None:
            parts.append(_format_default(col.default))
        if col.autoincrement:
            if col.primary_key and _is_integer_type(col.type_str):
                parts.append("AUTO_INCREMENT")
            else:
                logger.warning(
                    "列 %s.%s 类型 %s 不支持 AUTO_INCREMENT，已跳过",
                    change.table, col.name, col.type_str,
                )
        if col.primary_key:
            parts.append("PRIMARY KEY")
        if col.comment:
            escaped = col.comment.replace("'", "\\'")
            parts.append(f"COMMENT '{escaped}'")
        return " ".join(parts)
    elif change.change_type == "rename":
        return f"ALTER TABLE {table} RENAME COLUMN {_qt(change.old_name)} TO {_qt(change.column_name)}"
    elif change.change_type == "alter":
        col = change.definition
        assert col is not None
        parts = [f"ALTER TABLE {table} MODIFY COLUMN {_qt(col.name)} {col.type_str}"]
        if not col.nullable:
            parts.append("NOT NULL")
        if col.default is not None:
            parts.append(_format_default(col.default))
        if col.autoincrement:
            if _is_integer_type(col.type_str):
                parts.append("AUTO_INCREMENT")
            else:
                logger.warning(
                    "列 %s.%s 类型 %s 不支持 AUTO_INCREMENT，已跳过",
                    change.table, col.name, col.type_str,
                )
        if col.primary_key:
            parts.append("PRIMARY KEY")
        if col.comment:
            escaped = col.comment.replace("'", "\\'")
            parts.append(f"COMMENT '{escaped}'")
        return " ".join(parts)
    elif change.change_type == "drop":
        raise NotImplementedError("列删除不自动执行，请手动处理")
    raise ValueError(f"Unknown change_type: {change.change_type}")


def _compile_index_ddl(change: IndexChange) -> str:
    """将 IndexChange 编译为 DDL。"""
    idx = change.definition
    cols = _qteach(idx.columns)
    if change.change_type == "add":
        unique = "UNIQUE " if idx.unique else ""
        return f"CREATE {unique}INDEX {_qt(idx.name)} ON {_qt(change.table)} ({cols})"
    else:
        return f"DROP INDEX {_qt(idx.name)} ON {_qt(change.table)}"


def _compile_fk_ddl(change: ForeignKeyChange) -> str:
    """将 ForeignKeyChange 编译为 DDL。"""
    fk = change.definition
    cols = _qteach(fk.columns)
    ref_cols = _qteach(fk.ref_columns)
    if change.change_type == "add":
        ondelete = f" ON DELETE {fk.ondelete}" if fk.ondelete else ""
        return f"ALTER TABLE {_qt(change.table)} ADD FOREIGN KEY ({cols}) REFERENCES {_qt(fk.ref_table)} ({ref_cols}){ondelete}"
    else:
        name = fk.constraint_name or f"fk_{change.table}_{'_'.join(fk.columns)}"
        return f"ALTER TABLE {_qt(change.table)} DROP FOREIGN KEY {_qt(name)}"


def _affected_tables(diff: SchemaDiff) -> set[str]:
    """收集所有受变更影响的表名。"""
    tables: set[str] = set()
    for t in diff.tables_to_create:
        tables.add(t.name)
    for cc in diff.column_changes:
        tables.add(cc.table)
    for ic in diff.index_changes:
        tables.add(ic.table)
    for fc in diff.fk_changes:
        tables.add(fc.table)
    return tables


def _compute_schema_hash(metadata) -> str:
    """对 SQLModel metadata 中所有表的定义计算确定性 SHA-256 哈希。"""
    ordered_parts: list[str] = []
    for table_name in sorted(metadata.tables.keys()):
        if table_name.startswith("_"):
            continue
        table = metadata.tables[table_name]
        col_strs: list[str] = []
        for col in table.columns:
            nullable = "NULL" if col.nullable else "NOT NULL"
            default = str(col.server_default.arg.text) if col.server_default is not None else ""
            col_strs.append(f"{col.name}:{_type_to_string(col.type)}:{nullable}:{default}")
        idx_strs: list[str] = []
        for idx in sorted(table.indexes, key=lambda x: x.name or ""):
            idx_strs.append(f"{idx.name}:{sorted(idx.columns.keys())}:{idx.unique}")
        fk_strs: list[str] = []
        for fk in sorted(table.foreign_key_constraints, key=lambda x: x.name or ""):
            fk_strs.append(
                f"{fk.name}:{sorted(fk.columns.keys())}->{list(fk.elements)[0].column.table.name}:{sorted(str(e.column.name) for e in fk.elements)}:{fk.ondelete}"
            )
        ordered_parts.append(f"{table_name}({';'.join(col_strs)})({';'.join(idx_strs)})({';'.join(fk_strs)})")
    return hashlib.sha256("|".join(ordered_parts).encode()).hexdigest()


# ── wait_db_ready ──


def wait_db_ready(
    engine: Engine,
    max_tries: int = 300,
    wait_seconds: int = 1,
) -> None:
    """重试等待数据库就绪。每次重试间隔 1s，最多重试 300 次（5 分钟）。"""

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(max_tries),
        wait=tenacity.wait_fixed(wait_seconds),
        before=tenacity.before_log(logger, logging.INFO),
        after=tenacity.after_log(logger, logging.WARN),
    )
    def _ping():
        with Session(engine) as session:
            session.exec(select(1))

    try:
        _ping()
        logger.info("数据库连接就绪")
    except Exception as e:
        logger.error("数据库连接失败，已达最大重试次数: %s", e)
        raise


# ── SchemaSync orchestrator ──


class SchemaSync:
    """全自动 Schema 同步引擎。

    启动时完成：检测 schema 差异 → 自动备份 → DDL 执行 → 审计追踪。
    """

    def __init__(self, engine: Engine, backup_dir: str = "./backups",
                 retention_days: int = 30):
        self._engine = engine
        self._backup_dir = Path(backup_dir)
        self._retention_days = retention_days
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    def execute(self) -> SchemaDiff | None:
        """执行完整的 Schema 同步流程。

        Returns:
            SchemaDiff — 有变更时返回 diff 对象。
            None — schema 已是最新，无需同步。
        """
        current_hash = _compute_schema_hash(SQLModel.metadata)
        stored_hash = self._get_schema_version()
        if current_hash == stored_hash:
            logger.debug("Schema 哈希未变，跳过同步")
            return None

        target = inspect_target(SQLModel.metadata)
        current = inspect_current(self._engine)
        diff = compute_diff(target, current)

        if not self._has_changes(diff):
            self._set_schema_version(current_hash)
            return diff

        # 获取迁移锁 — 防止多实例并发执行 DDL
        if not self._acquire_migration_lock():
            logger.info("迁移锁已被其他实例持有，跳过本次同步")
            return None

        try:
            logger.info(
                "发现 schema 变更: %d 新表, %d 列变更, %d 索引变更, %d 外键变更",
                len(diff.tables_to_create), len(diff.column_changes),
                len(diff.index_changes), len(diff.fk_changes),
            )

            backup_paths = self.backup_tables(diff)
            change_count = self.apply_changes(diff)
            self.record_migration(diff, backup_paths)
            self._cleanup_old_backups()
            logger.info("Schema 同步完成，执行了 %d 个变更", change_count)
            return diff
        finally:
            self._release_migration_lock()

    @staticmethod
    def _has_changes(diff: SchemaDiff) -> bool:
        return bool(diff.tables_to_create or diff.column_changes
                    or diff.index_changes or diff.fk_changes)

    def _safe_execute_ddl(self, sql: str, description: str = "") -> bool:
        """安全执行 DDL，捕获"已存在"类错误以实现幂等性。

        Args:
            sql: DDL 语句
            description: 日志描述（表名.操作等）

        Returns:
            True — 执行成功或安全跳过（因目标已存在）
        """
        try:
            with self._engine.begin() as conn:
                conn.execute(sa_text(sql))
            return True
        except SQLAlchemyError as e:
            error_msg = str(e).upper()
            # MySQL 错误代码：1050=表已存在, 1060=列重复, 1061=索引重复, 1091=不存在
            # SQLite 错误消息：duplicate column name, already exists
            if any(code in error_msg for code in ("1050", "1060", "1061", "1091")):
                logger.warning("DDL 已执行过，安全跳过 [%s]: %s", description, e)
                return True
            if "DUPLICATE COLUMN NAME" in error_msg or "ALREADY EXISTS" in error_msg:
                logger.warning("DDL 已执行过，安全跳过 [%s]: %s", description, e)
                return True
            logger.error("DDL 执行失败 [%s]: %s", description, e)
            raise

    def _acquire_migration_lock(self) -> bool:
        """尝试获取迁移锁（原子 INSERT），防止多实例并发迁移。

        锁以 'migration_lock' 行存在 _schema_version 表中。
        INSERT 在 PK 冲突时抛出异常 → 锁已被持有。

        Returns:
            True — 成功获取锁
            False — 锁已被其他实例持有
        """
        self._ensure_schema_version_table()
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    sa_text("INSERT INTO _schema_version (skey, svalue) "
                            "VALUES ('migration_lock', 'locked')")
                )
            logger.debug("获取迁移锁成功")
            return True
        except SQLAlchemyError:
            logger.debug("迁移锁已被其他实例持有，跳过同步")
            return False

    def _release_migration_lock(self) -> None:
        """释放迁移锁。"""
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    sa_text("DELETE FROM _schema_version WHERE skey = 'migration_lock'")
                )
        except Exception as e:
            logger.warning("释放迁移锁失败: %s", e)

    def backup_tables(self, diff: SchemaDiff) -> dict[str, str]:
        """用纯 SQLAlchemy 备份受影响表的数据为 SQL 文件。跨平台，零外部依赖。"""
        tables = _affected_tables(diff)
        if not tables:
            return {}

        has_destructive = any(
            c.change_type in ("drop", "rename", "alter")
            for c in diff.column_changes
        ) or bool(diff.index_changes or diff.fk_changes)

        if not has_destructive:
            logger.info("仅新增列操作，跳过备份")
            return {}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self._backup_dir / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)

        from sqlalchemy import inspect as sa_inspect, text as sa_text

        paths: dict[str, str] = {}
        with self._engine.connect() as conn:
            inspector = sa_inspect(self._engine)
            existing_tables = set(inspector.get_table_names())
            for table in sorted(tables):
                if table not in existing_tables:
                    logger.debug("表 %s 尚不存在（将在 Phase 1 创建），跳过备份", table)
                    continue
                path = backup_dir / f"{table}.sql"
                cols = [c["name"] for c in inspector.get_columns(table)]
                col_list = ", ".join(f"`{c}`" for c in cols)

                # 流式写入备份文件 — 避免全表加载到内存（大表 OOM 防护）
                row_count = 0
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"-- Backup of table `{table}` — {datetime.now().isoformat()}\n")
                    f.write("SET NAMES utf8mb4;\n")
                    f.write(f"TRUNCATE TABLE `{table}`;\n\n")

                    # yield_per 分批从服务器获取行，不缓存全部到内存
                    for row in conn.execute(sa_text(f"SELECT * FROM `{table}`")).yield_per(500):
                        vals = []
                        for i, col in enumerate(cols):
                            v = row[i]
                            if v is None:
                                vals.append("NULL")
                            elif isinstance(v, (int, float)):
                                vals.append(str(v))
                            elif isinstance(v, bool):
                                vals.append("1" if v else "0")
                            elif isinstance(v, bytes):
                                vals.append(f"X'{v.hex()}'")
                            else:
                                escaped = str(v).replace("'", "''")
                                vals.append(f"'{escaped}'")
                        f.write(f"INSERT INTO `{table}` ({col_list}) VALUES ({', '.join(vals)});\n")
                        row_count += 1

                paths[table] = str(path)
                logger.info("备份完成: %s (%d 行)", path, row_count)

        return paths

    def apply_changes(self, diff: SchemaDiff) -> int:
        """执行 DDL 变更。返回执行的操作数。"""
        count = 0

        # Phase 1: CREATE TABLE（新表，幂等：if not exists 语义）
        for tdef in diff.tables_to_create:
            logger.info("创建表: %s", tdef.name)
            tdef_sa = SQLModel.metadata.tables.get(tdef.name)
            if tdef_sa is not None:
                try:
                    tdef_sa.create(self._engine)
                    count += 1
                except SQLAlchemyError as e:
                    if "1050" in str(e).upper():
                        logger.warning("表 %s 已存在，跳过创建", tdef.name)
                    else:
                        raise

        # Phase 2: 按表分组列变更（安全顺序：add → rename → alter）
        table_column_changes: dict[str, list[ColumnChange]] = {}
        for cc in diff.column_changes:
            table_column_changes.setdefault(cc.table, []).append(cc)

        order = {"add": 0, "rename": 1, "alter": 2}
        for table, changes in table_column_changes.items():
            changes_sorted = sorted(changes, key=lambda c: order.get(c.change_type, 9))
            for change in changes_sorted:
                sql = _compile_ddl(change)
                self._safe_execute_ddl(sql, f"{change.change_type} {table}.{change.column_name}")
                count += 1

        # Phase 3: 索引变更（先 drop 后 add）
        table_index_changes: dict[str, list[IndexChange]] = {}
        for ic in diff.index_changes:
            table_index_changes.setdefault(ic.table, []).append(ic)
        for table, changes in table_index_changes.items():
            for change in sorted(changes, key=lambda c: 0 if c.change_type == "drop" else 1):
                sql = _compile_index_ddl(change)
                self._safe_execute_ddl(sql, f"{change.change_type} index {table}.{change.definition.name}")
                count += 1

        # Phase 4: 外键变更（先 drop 后 add）
        table_fk_changes: dict[str, list[ForeignKeyChange]] = {}
        for fc in diff.fk_changes:
            table_fk_changes.setdefault(fc.table, []).append(fc)
        for table, changes in table_fk_changes.items():
            for change in sorted(changes, key=lambda c: 0 if c.change_type == "drop" else 1):
                sql = _compile_fk_ddl(change)
                self._safe_execute_ddl(sql, f"{change.change_type} fk {table}")
                count += 1

        return count

    def _cleanup_old_backups(self) -> None:
        """清理超过保留天数的备份目录。"""
        if self._retention_days <= 0:
            return
        cutoff = datetime.now().timestamp() - self._retention_days * 86400
        cleaned = 0
        for entry in self._backup_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.name == "_audit":
                continue
            try:
                dir_time = datetime.strptime(entry.name, "%Y%m%d_%H%M%S").timestamp()
            except ValueError:
                continue
            if dir_time < cutoff:
                import shutil
                shutil.rmtree(entry)
                cleaned += 1
                logger.info("清理过期备份: %s", entry)
        if cleaned:
            logger.info("清理了 %d 个过期备份目录", cleaned)

    def record_migration(self, diff: SchemaDiff, backup_paths: dict[str, str]) -> None:
        """记录迁移审计日志和 schema 哈希。"""
        audit_record = {
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "schema_hash": _compute_schema_hash(SQLModel.metadata),
            "changes": {
                "tables_created": [t.name for t in diff.tables_to_create],
                "columns_changed": [
                    f"{c.table}.{c.column_name}:{c.change_type}" for c in diff.column_changes
                ],
                "indexes_changed": [
                    f"{c.table}.{c.definition.name}:{c.change_type}" for c in diff.index_changes
                ],
                "foreign_keys_changed": [
                    f"{c.table}.{'_'.join(c.definition.columns)}:{c.change_type}"
                    for c in diff.fk_changes
                ],
            },
            "backups": backup_paths,
        }
        audit_dir = self._backup_dir / "_audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_file = audit_dir / f"{datetime.now():%Y%m%d_%H%M%S}_migration.json"
        audit_file.write_text(json.dumps(audit_record, indent=2, default=str), encoding="utf-8")
        logger.info("审计日志写入: %s", audit_file)

        self._set_schema_version(_compute_schema_hash(SQLModel.metadata))

    # ── Schema version tracking ──

    def _ensure_schema_version_table(self) -> None:
        """确保 _schema_version 表存在。兼容 MySQL 和 SQLite。"""
        if "sqlite" in str(self._engine.url):
            sql = (
                "CREATE TABLE IF NOT EXISTS _schema_version ("
                "  skey VARCHAR(64) PRIMARY KEY,"
                "  svalue VARCHAR(128) NOT NULL,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
        else:
            sql = (
                "CREATE TABLE IF NOT EXISTS _schema_version ("
                "  skey VARCHAR(64) PRIMARY KEY,"
                "  svalue VARCHAR(128) NOT NULL,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
                ")"
            )
        with self._engine.begin() as conn:
            conn.execute(sa_text(sql))

    def _get_schema_version(self) -> str | None:
        """读取数据库中的 schema 哈希。"""
        self._ensure_schema_version_table()
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT svalue FROM _schema_version WHERE skey = 'schema_hash'")
                ).first()
                return row[0] if row else None
        except Exception:
            return None

    def _set_schema_version(self, hash_val: str) -> None:
        """写入 schema 哈希。兼容 MySQL 和 SQLite。"""
        self._ensure_schema_version_table()
        with self._engine.begin() as conn:
            if "sqlite" in str(self._engine.url):
                conn.execute(sa_text(
                    "INSERT OR REPLACE INTO _schema_version (skey, svalue) VALUES ('schema_hash', :val)"
                ), {"val": hash_val})
            else:
                conn.execute(sa_text(
                    "INSERT INTO _schema_version (skey, svalue) VALUES ('schema_hash', :val) "
                    "ON DUPLICATE KEY UPDATE svalue = :val"
                ), {"val": hash_val})
