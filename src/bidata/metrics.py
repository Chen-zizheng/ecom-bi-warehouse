# -*- coding: utf-8 -*-
"""指标层：先建宽表视图，再在宽表上叠 8 个口径。

把「口径」写成视图而不是散在应用代码里，是为了让报表、导出、对账查的是同一份 SQL；
不然同一个月度 GMV，看板和导出的 Excel 对不上，排查起来是场灾难。
"""
from __future__ import annotations

from sqlalchemy import text

from . import db

# 有效订单：paid / shipped / completed / closed。退款单不整单剔除，只冲减 return 数量。
VALID = "('paid','shipped','completed','closed')"

WIDE = """
SELECT f.order_id, f.item_id, f.day_key, d.day, d.sales_month, d.weekday, d.quarter,
       p.category, p.sub_category, p.product_id, p.name AS product_name,
       f.customer_key, c.province, c.city, c.channel AS customer_channel,
       s.region, s.store_name,
       ch.channel, ch.channel_group,
       f.qty, f.gross_amount, f.discount_amount, f.net_amount,
       f.returned_qty, f.order_status
  FROM fact_sales f
  JOIN dim_date d ON d.day_key = f.day_key
  JOIN dim_product p ON p.product_key = f.product_key
  JOIN dim_customer c ON c.customer_key = f.customer_key
  JOIN dim_store s ON s.store_key = f.store_key
  JOIN dim_channel ch ON ch.channel_key = f.channel_key
 WHERE f.order_status NOT IN ('closed','refunded')
"""

VIEWS = {
    "v_sales_wide": WIDE,
    "v_kpi_total": """
        SELECT COUNT(DISTINCT order_id) AS orders,
               SUM(net_amount) AS gmv,
               SUM(net_amount) / NULLIF(COUNT(DISTINCT order_id), 0) AS avg_order_value,
               SUM(qty) AS units,
               SUM(net_amount) / NULLIF(SUM(qty), 0) AS avg_unit_price,
               SUM(returned_qty) * 1.0 / NULLIF(SUM(qty), 0) AS return_rate,
               SUM(discount_amount) / NULLIF(SUM(gross_amount), 0) AS discount_rate,
               SUM(gross_amount) AS gross,
               SUM(discount_amount) AS discount,
               SUM(returned_qty) AS returned_units,
               COUNT(DISTINCT customer_key) AS buyers,
               COUNT(DISTINCT product_key) AS skus
          FROM fact_sales""",
    "v_kpi_daily": """
        SELECT day_key, MIN(day) AS day,
               COUNT(DISTINCT order_id) AS orders,
               SUM(net_amount) AS gmv,
               SUM(returned_qty) * 1.0 / NULLIF(SUM(qty), 0) AS return_rate,
               SUM(discount_amount) / NULLIF(SUM(gross_amount), 0) AS discount_rate
          FROM fact_sales JOIN dim_date USING (day_key)
         GROUP BY day_key""",
    "v_category_month": """
        SELECT sales_month, category,
               SUM(net_amount) AS gmv,
               SUM(qty) AS units,
               SUM(returned_qty) * 1.0 / NULLIF(SUM(qty), 0) AS return_rate,
               SUM(discount_amount) / NULLIF(SUM(gross_amount), 0) AS discount_rate,
               COUNT(DISTINCT order_id) AS orders
          FROM v_sales_wide
         GROUP BY sales_month, category""",
    "v_region_month": """
        SELECT sales_month, region,
               SUM(net_amount) AS gmv,
               COUNT(DISTINCT order_id) AS orders,
               COUNT(DISTINCT customer_key) AS buyers
          FROM v_sales_wide
         GROUP BY sales_month, region""",
    "v_channel_month": """
        SELECT sales_month, channel, channel_group,
               SUM(net_amount) AS gmv,
               COUNT(DISTINCT order_id) AS orders
          FROM v_sales_wide
         GROUP BY sales_month, channel, channel_group""",
    "v_return_reason_month": """
        SELECT d.sales_month, r.reason,
               SUM(r.qty) AS returned_units,
               COUNT(*) AS return_lines
          FROM src_returns r
          JOIN src_orders o ON o.order_id = r.order_id
          JOIN dim_date d ON d.day = DATE(o.order_ts)
         GROUP BY d.sales_month, r.reason""",
    "v_customer_month": """
        SELECT sales_month, customer_key, MIN(day_key) AS min_day,
               SUM(net_amount) AS gmv, COUNT(DISTINCT order_id) AS orders
          FROM v_sales_wide
         GROUP BY sales_month, customer_key""",
    "v_customer_first": """
        SELECT f.customer_key, MIN(d.day_seq) AS first_seq
          FROM fact_sales f JOIN dim_date d ON d.day_key = f.day_key
         GROUP BY f.customer_key""",
    "v_customer_second": """
        SELECT f.customer_key, MIN(d.day_seq) AS second_seq
          FROM fact_sales f
          JOIN dim_date d ON d.day_key = f.day_key
          JOIN v_customer_first cf ON cf.customer_key = f.customer_key
         WHERE d.day_seq > cf.first_seq
         GROUP BY f.customer_key""",
    # 月内复购率：当月下过单的人里，当月下单 ≥2 次的比例。
    # 用首购人群做分母的话，客户都被"首购"吃光了，后几个月只剩个位数样本，
    # 曲线会假得像真的；月内口径每个月都重新取分母。
    "v_repeat_month": """
        SELECT sales_month,
               COUNT(*) AS buyers,
               SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END) AS repeat_buyers
          FROM v_customer_month
         GROUP BY sales_month""",
    "v_sku_sales": """
        SELECT product_key, SUM(qty) AS units, SUM(net_amount) AS gmv
          FROM fact_sales GROUP BY product_key""",
    "v_kpi_month": """
        SELECT d.sales_month,
               COUNT(DISTINCT f.order_id) AS orders,
               SUM(f.net_amount) AS gmv,
               SUM(f.net_amount) / NULLIF(COUNT(DISTINCT f.order_id), 0) AS avg_order_value,
               SUM(f.returned_qty) * 1.0 / NULLIF(SUM(f.qty), 0) AS return_rate,
               SUM(f.discount_amount) / NULLIF(SUM(f.gross_amount), 0) AS discount_rate,
               COUNT(DISTINCT f.customer_key) AS buyers,
               COUNT(DISTINCT f.product_key) AS active_skus
          FROM fact_sales f JOIN dim_date d ON d.day_key = f.day_key
         GROUP BY d.sales_month""",
}


def create_views(conn) -> list[str]:
    for name in ("v_sales_wide", "v_kpi_total", "v_kpi_daily", "v_category_month",
                 "v_region_month", "v_channel_month", "v_return_reason_month",
                 "v_customer_month", "v_customer_first", "v_customer_second",
                 "v_repeat_month",
                 "v_sku_sales", "v_kpi_month"):
        db.replace_view(conn, name, VIEWS[name])
    return list(VIEWS)


def build(engine=None, cfg=None) -> list[str]:
    from . import config as cfgmod
    cfg = cfg or cfgmod.load()
    engine = engine or db.make_engine(cfg.url)
    with db.session(engine) as conn:
        return create_views(conn)


def main(argv=None):
    names = build()
    print("created %d views: %s" % (len(names), ", ".join(names)))


if __name__ == "__main__":
    main()