# -*- coding: utf-8 -*-
"""连接、建表、幂等写入。MySQL 8 与 SQLite 都走这一套。"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import schema


def make_engine(url: str):
    if url.startswith("mysql"):
        return create_engine(url, pool_pre_ping=True, pool_recycle=3600, future=True)
    return create_engine(url, future=True)


def create_all(engine) -> None:
    schema.metadata.create_all(engine)


def upsert(conn, table, rows, key_cols, chunk: int = 2000) -> int:
    """按自然键覆盖写。返回处理行数。

    MySQL 与 SQLite 的语法不同，所以这里分两支；语义都是「存在则更新，不存在则插入」，
    这保证 ETL 重跑不会产生重复，也就保证了对账数字稳定。
    """
    if not rows:
        return 0
    # 只更新本次真的给了值的列：自增主键与没给的列不能被写进 ON DUPLICATE KEY UPDATE，
    # 否则 MySQL 会报 Unknown column 'new.xxx'。
    cols = [c for c in rows[0].keys() if c not in key_cols]
    total = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        if conn.dialect.name == "mysql":
            stmt = mysql_insert(table).values(part)
            upd = {c: stmt.inserted[c] for c in cols}
            conn.execute(stmt.on_duplicate_key_update(**upd))
        else:
            stmt = sqlite_insert(table).values(part)
            upd = {c: stmt.excluded[c] for c in cols}
            conn.execute(stmt.on_conflict_do_update(index_elements=key_cols, set_=upd))
        total += len(part)
    return total


def replace_view(conn, name: str, select_sql: str) -> None:
    """MySQL 支持 CREATE OR REPLACE，SQLite 只支持 DROP + CREATE。"""
    if conn.dialect.name == "mysql":
        conn.execute(text("CREATE OR REPLACE VIEW %s AS %s" % (name, select_sql)))
    else:
        conn.execute(text("DROP VIEW IF EXISTS %s" % name))
        conn.execute(text("CREATE VIEW %s AS %s" % (name, select_sql)))


@contextmanager
def session(engine):
    with engine.begin() as conn:
        yield conn


def count(conn, table_name: str) -> int:
    return int(conn.execute(text("SELECT COUNT(*) FROM %s" % table_name)).scalar() or 0)
