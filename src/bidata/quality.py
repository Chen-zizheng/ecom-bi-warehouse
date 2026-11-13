# -*- coding: utf-8 -*-
"""12 条质量规则，打的是仓里的表。源表脏不脏由 ETL 负责挡，这里验的是「装进来的对不对」。

每条规则返回 (检查行数, 失败行数, 明细)，失败行数大于 0 即 FAIL。结果落 dq_result，命令行和接口都能查。
"""
from __future__ import annotations

import time
from datetime import date, datetime

from sqlalchemy import text

from . import config as cfgmod
from . import db

RULES = [
    ("D1", "源明细要么进仓、要么被拒收", "fact_sales", """
        SELECT (SELECT COUNT(*) FROM src_order_items),
               (SELECT COUNT(*) FROM src_order_items i
                 WHERE NOT EXISTS (SELECT 1 FROM fact_sales f WHERE f.item_id = i.item_id)
                   AND NOT EXISTS (SELECT 1 FROM etl_reject e WHERE e.natural_key = i.item_id)),
               '既不进事实表也没留拒收记录的行，才是真丢数'"""),
    ("D2", "事实主键不重复", "fact_sales", """
        SELECT (SELECT COUNT(*) FROM fact_sales),
               (SELECT COALESCE(SUM(c - 1), 0) FROM (
                    SELECT item_id, COUNT(*) c FROM fact_sales GROUP BY item_id HAVING COUNT(*) > 1) t),
               '同一 item_id 出现多次'"""),
    ("D3", "事实引用的维度都存在", "fact_sales", """
        SELECT (SELECT COUNT(*) FROM fact_sales),
               (SELECT COUNT(*) FROM fact_sales f
                  LEFT JOIN dim_product p ON p.product_key = f.product_key
                  LEFT JOIN dim_customer c ON c.customer_key = f.customer_key
                  LEFT JOIN dim_store s ON s.store_key = f.store_key
                  LEFT JOIN dim_channel ch ON ch.channel_key = f.channel_key
                  LEFT JOIN dim_date d ON d.day_key = f.day_key
                WHERE p.product_key IS NULL OR c.customer_key IS NULL
                   OR s.store_key IS NULL OR ch.channel_key IS NULL OR d.day_key IS NULL),
               '外键悬空会让报表静默漏数'"""),
    ("D4", "净额与行金额自洽", "fact_sales", """
        SELECT COUNT(*), COALESCE(SUM(ABS(net_amount - (gross_amount - discount_amount)) > 0.01), 0),
               'gross - discount 应等于 net'
          FROM fact_sales"""),
    ("D5", "退货量不超过购买量", "fact_sales", """
        SELECT COUNT(*), COALESCE(SUM(returned_qty > qty), 0), 'returned_qty <= qty'
          FROM fact_sales"""),
    ("D6", "状态值在枚举内", "fact_sales", """
        SELECT COUNT(*),
               COALESCE(SUM(order_status NOT IN ('paid','shipped','completed','closed','refunded')), 0),
               '状态机之外的值不进事实表'
          FROM fact_sales"""),
    ("D7", "金额非负", "fact_sales", """
        SELECT COUNT(*), COALESCE(SUM(net_amount < 0 OR gross_amount < 0), 0), '负金额会污染汇总'
          FROM fact_sales"""),
    ("D8", "下单日期覆盖 dim_date", "fact_sales", """
        SELECT (SELECT COUNT(*) FROM fact_sales),
               (SELECT COUNT(*) FROM fact_sales f LEFT JOIN dim_date d ON d.day_key = f.day_key
                 WHERE d.day_key IS NULL), '日期维度缺一天，趋势图就断一格'"""),
    ("D9", "日期维度连续", "dim_date", None),
    ("D10", "源-仓金额对账", "fact_sales", """
        SELECT (SELECT COUNT(DISTINCT order_id) FROM fact_sales),
               (SELECT COUNT(*) FROM (
                    SELECT o.order_id
                    FROM src_orders o
                    JOIN (SELECT order_id, SUM(net_amount) net FROM fact_sales GROUP BY order_id) f
                      ON f.order_id = o.order_id
                    WHERE ABS(f.net - o.pay_amount) > 0.05) t),
               '按单对账，差额超过 5 分算失败'"""),
    ("D11", "有效订单都被装载", "fact_sales", """
        SELECT (SELECT COUNT(*) FROM src_orders),
               (SELECT COUNT(*) FROM src_orders o
                 WHERE o.status IN ('paid','shipped','completed','closed','refunded')
                   AND o.order_ts <= (SELECT MAX(order_ts) FROM src_orders)
                   AND NOT EXISTS (SELECT 1 FROM fact_sales f WHERE f.order_id = o.order_id)
                   -- 这里必须用 CONCAT：MySQL 里 || 是逻辑或，写成 o.order_id || '-%' 恒等于 0，
                   -- 条件就永远不成立，D11 会把被拒收的脏单算成"漏装"
                   AND NOT EXISTS (SELECT 1 FROM etl_reject e
                                    WHERE e.natural_key LIKE CONCAT(o.order_id, '-%'))),
               '有效订单要么进仓、要么被拒收'"""),
    ("D12", "水位线不超过源表最大值", "etl_state", """
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN last_updated_at > (SELECT MAX(updated_at) FROM src_order_items)
                                 THEN 1 ELSE 0 END), 0),
               '水位线跑飞会导致增量永远抽不到数据'
          FROM etl_state"""),
]


def _check_mysql_free_date(conn):
    """D9：日期维度的跨度必须等于行数。

    MySQL 的 DATEDIFF 与 SQLite 的 julianday 语法不同，为一条规则维护两套 SQL 不划算，
    跨度在 Python 里算。
    """
    checked, lo, hi = conn.execute(text("SELECT COUNT(*), MIN(day), MAX(day) FROM dim_date")).first()
    if isinstance(lo, str):
        lo, hi = date.fromisoformat(lo), date.fromisoformat(hi)
    span = (hi - lo).days + 1
    return int(checked), int(checked) - span, "跨度 %d 天" % span


def run(engine=None, cfg=None, run_id: str | None = None) -> list[dict]:
    cfg = cfg or cfgmod.load()
    engine = engine or db.make_engine(cfg.url)
    run_id = run_id or "DQ" + datetime.now().strftime("%Y%m%d%H%M%S")
    out: list[dict] = []
    t0 = time.perf_counter()
    with db.session(engine) as conn:
        for rule_id, name, target, sql in RULES:
            try:
                if sql is None:
                    checked, failed, detail = _check_mysql_free_date(conn)
                else:
                    checked, failed, detail = conn.execute(text(sql)).first()
                    checked, failed = int(checked or 0), int(failed or 0)
                status = "PASS" if failed == 0 else "FAIL"
            except Exception as exc:  # 规则写错也要留痕，不能静默跳过
                checked, failed, detail, status = 0, 0, "rule error: %s" % exc, "ERROR"
            out.append(dict(run_id=run_id, checked_at=datetime.now(), rule_id=rule_id,
                            rule_name=name, target=target, rows_checked=checked,
                            rows_failed=failed, status=status, detail=detail))
        conn.execute(db.schema.dq_result.insert(), out)
    elapsed = round(time.perf_counter() - t0, 2)
    for row in out:
        row["seconds"] = elapsed
    return out


def main(argv=None):
    rows = run()
    w = max(len(r["rule_name"]) for r in rows)
    for r in rows:
        print("%-4s %-*s checked=%-8d failed=%-6d %s"
              % (r["rule_id"], w, r["rule_name"], r["rows_checked"], r["rows_failed"], r["status"]))
    bad = [r for r in rows if r["status"] != "PASS"]
    print("\n%d rules, %d failed, %.2fs" % (len(rows), len(bad), rows[0]["seconds"]))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
