# -*- coding: utf-8 -*-
"""维表与事实表的装载。

两条路：full 重跑全部，incr 只处理 updated_at 超过水位的行。
事实表按 item_id 幂等写入，所以 incr 重跑多少次结果都一样。
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import text

from . import config as cfgmod
from . import db, schema

# 事实表收全部 5 种状态：取消单与退款单也要能追溯，只是不算进销售口径。
# 真正的「有效销售」过滤放在指标视图里（metrics.py 的 v_sales_wide），
# 这样改口径只改一处，不必重跑装载。
VALID_STATUS = ("paid", "shipped", "completed", "closed", "refunded")
CHANNEL_GROUP = {"天猫": "电商", "京东": "电商", "抖音": "内容电商", "小程序": "私域", "门店": "线下"}

REJECT_RULES = {
    "R1": "订单日期晚于今天",
    "R2": "订单金额为负",
    "R3": "订单状态不在枚举内",
    "R4": "订单行引用的商品不存在",
    "R5": "数量小于等于 0",
    "R6": "订单行找不到对应订单",
    "R7": "退货数量大于购买数量",
}


def _load_dim_date(conn, start: date, end: date) -> int:
    rows = []
    d = start
    while d <= end:
        rows.append(dict(
            day_key=int(d.strftime("%Y%m%d")), day=d, year=d.year, month=d.month,
            day_of_month=d.day, week_of_year=int(d.strftime("%W")),
            weekday=d.isoweekday(), is_weekend=d.isoweekday() >= 6,
            quarter=(d.month - 1) // 3 + 1, sales_month=d.strftime("%Y-%m"),
            day_seq=(d - start).days + 1,
        ))
        d += timedelta(days=1)
    return db.upsert(conn, schema.dim_date, rows, ["day_key"], chunk=500)


def _load_dims(conn) -> dict:
    """维表全量重拉再 upsert。

    20 万级的维表重拉一次不到半秒，比在这里维护 SCD2 便宜；
    真要做历史追踪，应该等业务真的开始问「这个客户当时的等级是什么」再上。
    """
    n = 0
    n += _load_dim_date(conn, date(2024, 1, 1), date(2027, 12, 31))
    n += db.upsert(conn, schema.dim_channel,
                   [dict(channel=c, channel_group=g) for c, g in CHANNEL_GROUP.items()], ["channel"])
    plans = (
        (schema.src_customers, schema.dim_customer, "customer_id",
         "customer_id, name, province, city, channel, register_ts"),
        (schema.src_products, schema.dim_product, "product_id",
         "product_id, name, category, sub_category, size, list_price"),
        (schema.src_stores, schema.dim_store, "store_id",
         "store_id, store_name, region, city"),
    )
    for src, dim, key, cols in plans:
        rows = []
        for r in conn.execute(text("SELECT %s FROM %s" % (cols, src.name))).mappings():
            row = {k: v for k, v in r.items() if k != "register_ts"}
            if "register_ts" in r:
                ts = r["register_ts"]
                if isinstance(ts, str):  # SQLite 把 DateTime 存成字符串，读回来要自己转
                    ts = datetime.fromisoformat(ts[:19])
                row["register_date"] = ts.date()
            rows.append(row)
        n += db.upsert(conn, dim, rows, [key])
    return {"rows": n}


def _key_maps(conn) -> dict:
    def mp(sql, *cols):
        out = {}
        for r in conn.execute(text(sql)):
            out[r[1]] = r[0]
        return out
    return {
        "customer": mp("SELECT customer_key, customer_id FROM dim_customer"),
        "product": mp("SELECT product_key, product_id FROM dim_product"),
        "store": mp("SELECT store_key, store_id FROM dim_store"),
        "channel": mp("SELECT channel_key, channel FROM dim_channel"),
    }


def _dt(v):
    """SQLite 把 DateTime 列读成字符串，比较和取 date() 之前先转回来。"""
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("T", " "))


def _return_qty(conn) -> dict:
    out = defaultdict(int)
    for r in conn.execute(text("SELECT item_id, SUM(qty) FROM src_returns GROUP BY item_id")):
        out[r[0]] = int(r[1] or 0)
    return out


def run(engine=None, cfg=None, mode: str = "incr") -> dict:
    cfg = cfg or cfgmod.load()
    engine = engine or db.make_engine(cfg.url)
    db.create_all(engine)
    run_id = "ETL" + datetime.now().strftime("%Y%m%d%H%M%S%f")  # 同一秒跑两次不能撞主键
    t0 = time.perf_counter()
    stats = dict(run_id=run_id, mode=mode, rows_in=0, rows_out=0, rejected=0)
    rejects = []

    with db.session(engine) as conn:
        conn.execute(schema.etl_run.insert().values(
            run_id=run_id, started_at=datetime.now(), mode=mode,
            rows_in=0, rows_out=0, status="running"))

        stats["dim_rows"] = _load_dims(conn)["rows"]
        keys = _key_maps(conn)
        ret_qty = _return_qty(conn)

        wm = None
        if mode == "incr":
            row = conn.execute(text("SELECT last_updated_at FROM etl_state WHERE source='src_order_items'")).first()
            wm = _dt(row[0]) if row else None

        sql = text("""
            SELECT i.item_id, i.order_id, i.product_id, i.qty, i.unit_price, i.discount,
                   o.order_ts, o.status, o.pay_amount, o.customer_id, o.store_id, o.channel
            FROM src_order_items i
            LEFT JOIN src_orders o ON o.order_id = i.order_id
            WHERE (:isfull = 1) OR i.updated_at > :wm OR o.updated_at > :wm
        """)
        params = {"isfull": 1 if wm is None else 0, "wm": wm or datetime(1970, 1, 1)}
        result = conn.execute(sql, params)

        batch, now, today = [], datetime.now(), datetime.now()
        orders_seen, fact_items = set(), 0

        def flush():
            nonlocal batch, fact_items
            if batch:
                fact_items += db.upsert(conn, schema.fact_sales, batch, ["item_id"])
                batch = []

        for r in result.mappings():
            stats["rows_in"] += 1
            rule = reason = None
            ts = _dt(r["order_ts"])
            if r["order_id"] is None or ts is None:
                rule, reason = "R6", "订单行找不到对应订单 %s" % r["order_id"]
            elif r["status"] not in VALID_STATUS:
                rule, reason = "R3", "状态 %s 不在枚举内" % r["status"]
            elif ts > today:
                rule, reason = "R1", "下单时间 %s 晚于今天" % ts
            elif float(r["pay_amount"]) < 0:
                rule, reason = "R2", "订单金额 %s 为负" % r["pay_amount"]
            elif r["product_id"] not in keys["product"]:
                rule, reason = "R4", "商品 %s 不存在于维表" % r["product_id"]
            elif int(r["qty"]) <= 0:
                rule, reason = "R5", "数量 %s" % r["qty"]

            if rule:
                rejects.append(dict(run_id=run_id, source="src_order_items",
                                    natural_key=r["item_id"], rule_id=rule,
                                    reason=reason[:120], seen_at=now))
                continue

            qty = int(r["qty"])
            unit = float(r["unit_price"])
            disc = float(r["discount"])
            gross = round(unit * qty, 2)
            discount_amt = round(disc * qty, 2)
            net = round(gross - discount_amt, 2)
            returned = min(ret_qty.get(r["item_id"], 0), qty)
            if ret_qty.get(r["item_id"], 0) > qty:
                rejects.append(dict(run_id=run_id, source="src_returns",
                                    natural_key=r["item_id"], rule_id="R7",
                                    reason="退货 %d 件多于购买 %d 件，按购买量截断"
                                           % (ret_qty[r["item_id"]], qty), seen_at=now))
            batch.append(dict(
                order_id=r["order_id"], item_id=r["item_id"],
                day_key=int(ts.strftime("%Y%m%d")),
                product_key=keys["product"][r["product_id"]],
                customer_key=keys["customer"][r["customer_id"]],
                store_key=keys["store"][r["store_id"]],
                channel_key=keys["channel"][r["channel"]],
                qty=qty, gross_amount=gross, discount_amount=discount_amt,
                net_amount=net,
                ship_fee_alloc=0.0,
                returned_qty=returned, order_status=r["status"],
                updated_at=ts,
            ))
            orders_seen.add(r["order_id"])
            if len(batch) >= 2000:
                flush()
        flush()

        stats["rows_out"] = fact_items
        stats["rejected"] = len(rejects)
        if rejects:
            conn.execute(schema.etl_reject.insert(), rejects)
            stats["reject_rules"] = {k: sum(1 for x in rejects if x["rule_id"] == k)
                                     for k in sorted({x["rule_id"] for x in rejects})}

        max_ts = _dt(conn.execute(text("SELECT MAX(updated_at) FROM src_order_items")).scalar())
        conn.execute(text("UPDATE etl_run SET finished_at=:f, rows_in=:i, rows_out=:o, status='success', note=:n "
                          "WHERE run_id=:r"),
                     {"f": datetime.now(), "i": stats["rows_in"], "o": stats["rows_out"],
                      "n": "rejected=%d mode=%s" % (stats["rejected"], mode), "r": run_id})
        conn.execute(text("DELETE FROM etl_state WHERE source='src_order_items'"))
        conn.execute(schema.etl_state.insert().values(
            source="src_order_items", last_updated_at=max_ts,
            last_run_at=datetime.now(), rows_written=fact_items))

    stats["seconds"] = round(time.perf_counter() - t0, 2)
    return stats


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="装载维度与事实")
    ap.add_argument("--mode", choices=["full", "incr"], default="incr")
    a = ap.parse_args(argv)
    s = run(mode=a.mode)
    for k, v in s.items():
        print("%-12s %s" % (k, v))


if __name__ == "__main__":
    main()