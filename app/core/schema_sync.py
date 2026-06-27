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
