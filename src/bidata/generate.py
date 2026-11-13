# -*- coding: utf-8 -*-
"""造源数据：5 张业务表 + 一批故意做脏的行。

脏数据是刻意留的，质量规则要能抓到才算数（见 quality.py 的 D6、D7、D9 等）。
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta

from faker import Faker
from sqlalchemy import text

from . import config as cfgmod
from . import db, schema

CATEGORIES = {
    "服饰": ["T恤", "卫衣", "牛仔裤", "连衣裙", "羽绒服"],
    "鞋靴": ["运动鞋", "帆布鞋", "靴子", "凉鞋"],
    "箱包": ["双肩包", "手提包", "行李箱"],
    "家居": ["床品", "收纳", "厨具", "灯具"],
    "数码": ["耳机", "键盘", "充电宝", "支架"],
    "美妆": ["护肤", "彩妆", "香氛"],
    "食品": ["坚果", "茶饮", "零食"],
    "运动": ["瑜伽垫", "哑铃", "护具"],
}
SIZES = ["S", "M", "L", "XL", "均码"]
PROVINCES = {
    "华东": ["上海", "杭州", "南京", "苏州", "合肥"],
    "华南": ["广州", "深圳", "东莞", "厦门"],
    "华北": ["北京", "天津", "石家庄"],
    "华中": ["武汉", "长沙", "郑州"],
    "西南": ["成都", "重庆", "昆明"],
    "东北": ["沈阳", "大连", "哈尔滨"],
}
CHANNELS = [("天猫", "电商"), ("京东", "电商"), ("抖音", "内容电商"), ("小程序", "私域"), ("门店", "线下")]
STATUS = ["paid", "shipped", "completed", "closed", "refunded"]
RETURN_REASONS = ["尺码不符", "质量问题", "颜色差异", "物流破损", "不想要了"]

COUNT = {"customers": 20000, "products": 2000, "stores": 30, "orders": 300000}


def _rand_ts(rnd: random.Random, start: datetime, end: datetime) -> datetime:
    return start + timedelta(seconds=rnd.randint(0, int((end - start).total_seconds())))


def build(cfg=None, append_orders: int = 0, dirty: bool = True) -> dict:
    """写源表。append_orders>0 时只追加新订单，用来演示增量抽取。"""
    cfg = cfg or cfgmod.load()
    rnd = random.Random(cfg.seed)
    fake = Faker("zh_CN")
    Faker.seed(cfg.seed)
    scale = cfg.source_scale
    start = datetime.fromisoformat(cfg.start_date)
    end = datetime.fromisoformat(cfg.end_date) + timedelta(hours=23)

    engine = db.make_engine(cfg.url)
    db.create_all(engine)
    written = {}

    with db.session(engine) as conn:
        # ---------- 全量：维度源
        if append_orders == 0:
            stores = []
            for i in range(COUNT["stores"]):
                region = list(PROVINCES)[i % len(PROVINCES)]
                stores.append(dict(
                    store_id="S%04d" % (i + 1),
                    store_name="%s旗舰店" % PROVINCES[region][i % len(PROVINCES[region])],
                    region=region, city=PROVINCES[region][i % len(PROVINCES[region])],
                    open_date=(datetime(2023, 1, 1) + timedelta(days=rnd.randint(0, 700))).date(),
                    updated_at=datetime(2025, 1, 1),
                ))
            written["src_stores"] = db.upsert(conn, schema.src_stores, stores, ["store_id"])

            products = []
            for i in range(int(COUNT["products"] * scale)):
                cat = rnd.choice(list(CATEGORIES))
                sub = rnd.choice(CATEGORIES[cat])
                products.append(dict(
                    product_id="P%06d" % (i + 1),
                    name="%s %s %02d" % (sub, cat, i + 1),
                    category=cat, sub_category=sub,
                    size=rnd.choice(SIZES) if cat in ("服饰", "鞋靴") else "均码",
                    list_price=round(rnd.uniform(39, 1299), 2),
                    updated_at=datetime(2025, 1, 1),
                ))
            written["src_products"] = db.upsert(conn, schema.src_products, products, ["product_id"])

            customers = []
            for i in range(int(COUNT["customers"] * scale)):
                region = rnd.choice(list(PROVINCES))
                customers.append(dict(
                    customer_id="C%07d" % (i + 1),
                    name=fake.last_name() + fake.first_name(),
                    province=region, city=rnd.choice(PROVINCES[region]),
                    channel=rnd.choice(CHANNELS)[0],
                    register_ts=_rand_ts(rnd, datetime(2024, 1, 1), end),
                    updated_at=datetime(2025, 1, 1),
                ))
            written["src_customers"] = db.upsert(conn, schema.src_customers, customers, ["customer_id"])
        else:
            products = [dict(r) for r in conn.execute(text("SELECT product_id, list_price FROM src_products")).mappings()]
            customers = [dict(r) for r in conn.execute(text("SELECT customer_id FROM src_customers")).mappings()]

        # ---------- 订单（全量或增量追加）
        n_orders = append_orders or int(COUNT["orders"] * scale)
        base = int(conn.execute(text("SELECT COALESCE(MAX(CAST(SUBSTR(order_id, 2) AS SIGNED)), 0) FROM src_orders")).scalar() or 0) \
            if engine.dialect.name == "mysql" else int(conn.execute(text("SELECT COALESCE(MAX(CAST(SUBSTR(order_id, 2) AS INTEGER)), 0) FROM src_orders")).scalar() or 0)
        order_rows, item_rows, return_rows = [], [], []
        r7_target = None
        now = datetime.now()
        for k in range(n_orders):
            oid = "O%09d" % (base + k + 1)
            cust = rnd.choice(customers)
            ts = _rand_ts(rnd, start, end) if append_orders == 0 else now - timedelta(minutes=rnd.randint(0, 600))
            chan = rnd.choice(CHANNELS)[0]
            lines = []
            total_pay = 0.0
            for li in range(rnd.randint(1, 4)):
                prod = rnd.choice(products)
                qty = rnd.choices([1, 2, 3], weights=[80, 15, 5])[0]
                price = float(prod["list_price"])
                disc = round(price * rnd.choice([0, 0.05, 0.1, 0.2, 0.3]), 2)
                total_pay += round((price - disc) * qty, 2)
                lines.append(("O%09d-%02d" % (base + k + 1, li + 1), oid, prod["product_id"], qty, price, disc))
            ship = 0.0 if total_pay >= 99 else 8.0
            status = rnd.choices(STATUS, weights=[8, 20, 62, 6, 4])[0]
            order_rows.append(dict(
                order_id=oid, customer_id=cust["customer_id"],
                store_id="S%04d" % rnd.randint(1, COUNT["stores"]),
                channel=chan, order_ts=ts, status=status,
                pay_amount=round(total_pay, 2), ship_fee=ship,
                updated_at=now,   # 写入时刻，不是业务时刻：增量靠它跟水位线比
            ))
            for item_id, o, pid, qty, price, disc in lines:
                item_rows.append(dict(item_id=item_id, order_id=o, product_id=pid, qty=qty,
                                      unit_price=price, discount=disc,
                                      updated_at=now))
                if status in ("completed", "refunded") and r7_target is None:
                    r7_target = (o, item_id, qty)  # R7 的靶子：一条真实成交的行
                if status in ("completed", "refunded") and rnd.random() < 0.035:
                    return_rows.append(dict(
                        return_id="R%09d" % (len(return_rows) + 1), order_id=o, item_id=item_id,
                        qty=1 if qty > 1 else 1, reason=rnd.choice(RETURN_REASONS),
                        return_ts=ts + timedelta(days=rnd.randint(1, 20)),
                        updated_at=ts + timedelta(days=rnd.randint(1, 20)),
                    ))

        # ---------- 刻意做脏的行：每条单独立，不复用真实订单号，
        # 否则 upsert 会把真实订单覆盖掉，"注入脏数据"就变成了"篡改数据"。
        if dirty and append_orders == 0:
            def mk_dirty(oid, **over):
                item = dict(item_id=oid + "-01", order_id=oid, product_id=products[0]["product_id"],
                            qty=1, unit_price=100.0, discount=0.0, updated_at=now)
                order = dict(order_id=oid, customer_id=customers[0]["customer_id"], store_id="S0001",
                             channel="天猫", order_ts=now - timedelta(days=1), status="paid",
                             pay_amount=100.0, ship_fee=0.0, updated_at=now)
                order.update(over)
                order_rows.append(order)
                item_rows.append(item)
                return item

            mk_dirty("O999999991", order_ts=datetime(2030, 1, 1))       # R1 未来日期
            mk_dirty("O999999992", pay_amount=-99.0)                     # R2 负金额
            mk_dirty("O999999993", status="unknwon")                     # R3 非法状态
            mk_dirty("O999999994")["product_id"] = "P999999"             # R4 商品不存在
            mk_dirty("O999999995")["qty"] = 0                            # R5 数量 0
            mk_dirty("O999999996")["order_id"] = "O999999998"            # R6 孤儿订单行
            if r7_target:  # R7 退得比买得多，挂在真实订单行上，这样装载时会走到退货截断分支
                ro, ri, rq = r7_target
                return_rows.append(dict(return_id="R999999999", order_id=ro, item_id=ri,
                                        qty=rq + 3, reason="质量问题",
                                        return_ts=now, updated_at=now))

        written["src_orders"] = db.upsert(conn, schema.src_orders, order_rows, ["order_id"])
        written["src_order_items"] = db.upsert(conn, schema.src_order_items, item_rows, ["item_id"])
        written["src_returns"] = db.upsert(conn, schema.src_returns, return_rows, ["return_id"])

    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成电商源数据")
    ap.add_argument("--config")
    ap.add_argument("--scale", type=float, help="订单/客户/商品量的缩放系数")
    ap.add_argument("--append-orders", type=int, default=0, help="只追加 N 张新订单（演示增量）")
    ap.add_argument("--clean", action="store_true", help="不注入脏数据")
    a = ap.parse_args(argv)
    cfg = cfgmod.load(a.config)
    if a.scale:
        cfg.source_scale = a.scale
    w = build(cfg, append_orders=a.append_orders, dirty=not a.clean)
    for k, v in w.items():
        print("%-16s %8d" % (k, v))


if __name__ == "__main__":
    main()
