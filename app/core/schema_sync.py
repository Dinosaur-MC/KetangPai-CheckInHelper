"""Fully automatic schema synchronization — replaces _migrate()."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, inspect as sa_inspect
from sqlmodel import SQLModel

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
    max_distance: int = 3,
) -> list[tuple[str, str]]:
    """检测可能的列改名对。"""
    results: list[tuple[str, str, int]] = []
    for old_name in sorted(deleted_names):
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
    for old_name, new_name, _ in results:
        if new_name not in used_new:
            final.append((old_name, new_name))
            used_new.add(new_name)
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
        renames = _detect_rename(deleted, added, current_types, target_types)
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

        # 索引对比
        current_idx_map = {(idx.name, tuple(idx.columns)): idx for idx in cur.indexes}
        target_idx_map = {(idx.name, tuple(idx.columns)): idx for idx in tdef.indexes}
        for key, idx in target_idx_map.items():
            if key not in current_idx_map:
                diff.index_changes.append(IndexChange(
                    table=name, change_type="add", definition=idx,
                ))
        for key, idx in current_idx_map.items():
            if key not in target_idx_map:
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
    """将 SQLAlchemy 类型规范化为统一字符串表示。"""
    raw = str(col_type)
    return raw.upper()


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
                autoincrement=col.autoincrement or (col.primary_key and col.autoincrement is not False),
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
            elements = list(fk.elements)
            if elements:
                ref_col_table = elements[0].column.table
                fks.append(ForeignKeyDef(
                    columns=cols,
                    ref_table=ref_col_table.name,
                    ref_columns=[str(col.name) for col in ref_col_table.columns],
                    ondelete=fk.ondelete or "",
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
            ))

        result[table_name] = TableDef(name=table_name, columns=columns, indexes=indexes, foreign_keys=fks)
    return result
