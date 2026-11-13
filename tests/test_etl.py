# -*- coding: utf-8 -*-
from sqlalchemy import text

from bidata import etl, generate


def _scalar(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def test_fact_loaded_and_dirty_rows_rejected(ctx):
    engine, full = ctx["engine"], ctx["full"]
    assert full["rows_out"] > 0
    assert full["rejected"] >= 7
    rules = set(r[0] for r in engine.connect().execute(
        text("SELECT DISTINCT rule_id FROM etl_reject")).all())
    assert {"R1", "R2", "R3", "R4", "R5", "R6", "R7"} <= rules
    # 事实表收 5 种状态（取消与退款也要能追溯），脏状态 unknwon 必须被挡在门外
    assert _scalar(engine, "SELECT COUNT(*) FROM fact_sales WHERE order_status NOT IN "
                           "('paid','shipped','completed','closed','refunded')") == 0
    assert _scalar(engine, "SELECT COUNT(*) FROM fact_sales f LEFT JOIN dim_product p "
                           "ON p.product_key = f.product_key WHERE p.product_key IS NULL") == 0


def test_incremental_is_idempotent(ctx):
    engine, cfg = ctx["engine"], ctx["cfg"]
    before = _scalar(engine, "SELECT COUNT(*) FROM fact_sales")
    again = etl.run(engine, cfg, mode="incr")
    after = _scalar(engine, "SELECT COUNT(*) FROM fact_sales")
    assert before == after
    assert again["rows_out"] == 0


def test_incremental_picks_up_new_orders(ctx):
    engine, cfg = ctx["engine"], ctx["cfg"]
    generate.build(cfg, append_orders=25)
    run = etl.run(engine, cfg, mode="incr")
    assert run["rows_in"] > 25
    assert run["rows_out"] == run["rows_in"]
