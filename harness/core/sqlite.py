"""当前 SQLite schema 的唯一初始化入口，不补表或迁移已有结构。"""
from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from contextlib import closing

from harness.i18n import tr
from .errors import HarnessError


def _catalog(db: sqlite3.Connection, schema: str = 'main') -> tuple[tuple, ...]:
    """只比较应用结构；SQLite 自建的序列和统计表由数据库维护。"""
    rows = []
    for row in db.execute(f"SELECT type,name,tbl_name,sql FROM {schema}.sqlite_schema WHERE name NOT GLOB 'sqlite_*'"):
        # 忽略排版空白，但保留引号中的默认值和索引表达式原文。
        sql = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]|\s+",
                     lambda match: ' ' if match[0].isspace() else match[0], row[3]).strip()
        rows.append((*row[:3], sql))
    return tuple(sorted(rows))


@lru_cache(maxsize=16)
def _definition(script: str) -> tuple[int, tuple[str, ...], tuple[tuple, ...]]:
    """用 SQLite 自身解析当前声明，不另写 SQL 解析器或复制字段清单。"""
    with closing(sqlite3.connect(':memory:')) as reference:
        reference.executescript(script)
        statements = tuple(row[0] for row in reference.execute(
            "SELECT sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY rowid"))
        return reference.execute('PRAGMA user_version').fetchone()[0], statements, _catalog(reference)


def prepare_database(db: sqlite3.Connection, script: str, *, initialize: bool = True, schema: str = 'main') -> None:
    """空库原子初始化；已有库严格校验当前结构，异常时不改写数据库。"""
    if not isinstance(schema, str) or not re.fullmatch('[a-z][a-z0-9_]{0,30}', schema) or schema != 'main' and initialize:
        raise ValueError(tr('runtime.validation.0374d9ad72ed'))
    version, statements, expected = _definition(script)
    owns_transaction = initialize and not db.in_transaction
    if initialize:
        db.execute('BEGIN IMMEDIATE' if owns_transaction else 'SAVEPOINT current_schema')
    try:
        actual = _catalog(db, schema)
        actual_version = db.execute(f'PRAGMA {schema}.user_version').fetchone()[0]
        if initialize and not actual and actual_version == 0:
            for statement in statements:
                db.execute(statement)
            db.execute(f'PRAGMA user_version={version}')
        elif actual != expected or actual_version != version:
            raise HarnessError(tr('storage.schema.invalid'), code='storage/schema')
        if owns_transaction:
            db.commit()
        elif initialize:
            db.execute('RELEASE current_schema')
    except BaseException:
        if owns_transaction:
            db.rollback()
        elif initialize:
            db.execute('ROLLBACK TO current_schema')
            db.execute('RELEASE current_schema')
        raise
