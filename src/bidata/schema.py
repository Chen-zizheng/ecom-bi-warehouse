# -*- coding: utf-8 -*-
"""源表、维度、事实与运维表的定义。

用 SQLAlchemy Core 描述，同一份定义能在 MySQL 8 与 SQLite 上建表，
测试因此不需要拉一个 MySQL 容器。
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    DECIMAL,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql

MONEY = DECIMAL(14, 2)
# MySQL 的 DATETIME 默认只到秒，同一秒内写入的行会跟水位线撞平，
# 增量就再也抽不到；fsp=6 保留微秒。SQLite 本来存的就是字符串，这里不用管。
TS = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
metadata = MetaData()

# ---------------------------------------------------------------- 源表（贴源层）
src_customers = Table(
    "src_customers", metadata,
    Column("customer_id", String(16), primary_key=True),
    Column("name", String(32), nullable=False),
    Column("province", String(16), nullable=False),
    Column("city", String(16), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("register_ts", DateTime, nullable=False),
    Column("updated_at", TS, nullable=False),
)

src_products = Table(
    "src_products", metadata,
    Column("product_id", String(16), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("category", String(16), nullable=False),
    Column("sub_category", String(16), nullable=False),
    Column("size", String(8)),
    Column("list_price", MONEY, nullable=False),
    Column("updated_at", TS, nullable=False),
)

src_stores = Table(
    "src_stores", metadata,
    Column("store_id", String(16), primary_key=True),
    Column("store_name", String(32), nullable=False),
    Column("region", String(16), nullable=False),
    Column("city", String(16), nullable=False),
    Column("open_date", Date, nullable=False),
    Column("updated_at", TS, nullable=False),
)

src_orders = Table(
    "src_orders", metadata,
    Column("order_id", String(24), primary_key=True),
    Column("customer_id", String(16), nullable=False),
    Column("store_id", String(16), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("order_ts", DateTime, nullable=False),
    Column("status", String(16), nullable=False),
    Column("pay_amount", MONEY, nullable=False),
    Column("ship_fee", MONEY, nullable=False),
    Column("updated_at", TS, nullable=False),
)

src_order_items = Table(
    "src_order_items", metadata,
    Column("item_id", String(24), primary_key=True),
    Column("order_id", String(24), nullable=False),
    Column("product_id", String(16), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("unit_price", MONEY, nullable=False),
    Column("discount", MONEY, nullable=False),
    Column("updated_at", TS, nullable=False),
)

src_returns = Table(
    "src_returns", metadata,
    Column("return_id", String(24), primary_key=True),
    Column("order_id", String(24), nullable=False),
    Column("item_id", String(24), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("reason", String(16), nullable=False),
    Column("return_ts", DateTime, nullable=False),
    Column("updated_at", TS, nullable=False),
)

# ---------------------------------------------------------------- 维度
dim_date = Table(
    "dim_date", metadata,
    Column("day_key", Integer, primary_key=True),
    Column("day", Date, nullable=False),
    Column("year", SmallInteger, nullable=False),
    Column("month", SmallInteger, nullable=False),
    Column("day_of_month", SmallInteger, nullable=False),
    Column("week_of_year", SmallInteger, nullable=False),
    Column("weekday", SmallInteger, nullable=False),
    Column("is_weekend", Boolean, nullable=False),
    Column("quarter", SmallInteger, nullable=False),
    Column("sales_month", String(7), nullable=False),
    # 连续日序号：算「30 天内又买了一次」时直接做整数减法，
    # 不用去写 DATEDIFF（MySQL）或 julianday（SQLite）两套日期函数
    Column("day_seq", Integer, nullable=False),
)

dim_customer = Table(
    "dim_customer", metadata,
    Column("customer_key", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("customer_id", String(16), nullable=False, unique=True),
    Column("name", String(32), nullable=False),
    Column("province", String(16), nullable=False),
    Column("city", String(16), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("register_date", Date, nullable=False),
)

dim_product = Table(
    "dim_product", metadata,
    Column("product_key", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("product_id", String(16), nullable=False, unique=True),
    Column("name", String(64), nullable=False),
    Column("category", String(16), nullable=False),
    Column("sub_category", String(16), nullable=False),
    Column("size", String(8)),
    Column("list_price", MONEY, nullable=False),
)

dim_store = Table(
    "dim_store", metadata,
    Column("store_key", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("store_id", String(16), nullable=False, unique=True),
    Column("store_name", String(32), nullable=False),
    Column("region", String(16), nullable=False),
    Column("city", String(16), nullable=False),
)

dim_channel = Table(
    "dim_channel", metadata,
    Column("channel_key", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("channel", String(16), nullable=False, unique=True),
    Column("channel_group", String(16), nullable=False),
)

# ---------------------------------------------------------------- 事实
fact_sales = Table(
    "fact_sales", metadata,
    Column("sales_key", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("order_id", String(24), nullable=False),
    Column("item_id", String(24), nullable=False),
    Column("day_key", Integer, nullable=False),
    Column("product_key", BigInteger, nullable=False),
    Column("customer_key", BigInteger, nullable=False),
    Column("store_key", BigInteger, nullable=False),
    Column("channel_key", BigInteger, nullable=False),
    Column("qty", Integer, nullable=False),
    Column("gross_amount", MONEY, nullable=False),
    Column("discount_amount", MONEY, nullable=False),
    Column("net_amount", MONEY, nullable=False),
    Column("ship_fee_alloc", MONEY, nullable=False),
    Column("returned_qty", Integer, nullable=False, default=0),
    Column("order_status", String(16), nullable=False),
    Column("updated_at", TS, nullable=False),
    UniqueConstraint("item_id", name="uq_fact_sales_item"),
)

# 角色表：一份报表服务多角色，接口层按角色注入过滤条件（见 api.py 的 row_filter）
dim_role = Table(
    "dim_role", metadata,
    Column("role_name", String(32), primary_key=True),
    Column("region_scope", String(64)),      # 逗号分隔，NULL 表示不限
    Column("category_scope", String(128)),
    Column("note", String(64)),
)

# ---------------------------------------------------------------- 运维
etl_state = Table(
    "etl_state", metadata,
    Column("source", String(32), primary_key=True),
    Column("last_updated_at", TS),
    Column("last_run_at", DateTime),
    Column("rows_written", Integer, nullable=False, default=0),
)

etl_run = Table(
    "etl_run", metadata,
    Column("run_id", String(32), primary_key=True),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("mode", String(16), nullable=False),
    Column("rows_in", Integer, nullable=False, default=0),
    Column("rows_out", Integer, nullable=False, default=0),
    Column("status", String(16), nullable=False),
    Column("note", Text),
)

etl_reject = Table(
    "etl_reject", metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("run_id", String(32), nullable=False),
    Column("source", String(32), nullable=False),
    Column("natural_key", String(32), nullable=False),
    Column("rule_id", String(8), nullable=False),
    Column("reason", String(128), nullable=False),
    Column("seen_at", DateTime, nullable=False),
)

dq_result = Table(
    "dq_result", metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("run_id", String(32), nullable=False),
    Column("checked_at", DateTime, nullable=False),
    Column("rule_id", String(8), nullable=False),
    Column("rule_name", String(48), nullable=False),
    Column("target", String(48), nullable=False),
    Column("rows_checked", BigInteger, nullable=False),
    Column("rows_failed", BigInteger, nullable=False),
    Column("status", String(8), nullable=False),
    Column("detail", Text),
)

ALL_TABLES = [
    src_customers, src_products, src_stores, src_orders, src_order_items, src_returns,
    dim_date, dim_customer, dim_product, dim_store, dim_channel, dim_role,
    fact_sales, etl_state, etl_run, etl_reject, dq_result,
]