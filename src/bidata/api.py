# -*- coding: utf-8 -*-
"""报表接口。

行级权限放在这一层做：数据库里不做行级安全，因为报表服务是唯一入口，
而 MySQL 的 RLS 要按用户建视图，加一个角色就多一堆视图，运维成本立刻上去。
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import config as cfgmod
from . import db

cfg = cfgmod.load()
engine = db.make_engine(cfg.url)
app = FastAPI(title="电商 BI 报表服务", version="1.0.0")


def _role(conn, role: str | None) -> tuple[str, dict]:
    """把角色翻成 WHERE 片段。未知角色直接 403，不给「查不到就返回全部」的口子。"""
    if not role:
        return "1=1", {}
    row = conn.execute(text("SELECT region_scope, category_scope FROM dim_role WHERE role_name = :r"),
                       {"r": role}).first()
    if row is None:
        raise HTTPException(status_code=403, detail="unknown role: %s" % role)
    parts, params = [], {}
    if row[0]:
        regions = [x.strip() for x in row[0].split(",") if x.strip()]
        marks = ", ".join(":rg%d" % i for i in range(len(regions)))
        parts.append("region IN (%s)" % marks)
        params.update({"rg%d" % i: v for i, v in enumerate(regions)})
    if row[1]:
        cats = [x.strip() for x in row[1].split(",") if x.strip()]
        marks = ", ".join(":ct%d" % i for i in range(len(cats)))
        parts.append("category IN (%s)" % marks)
        params.update({"ct%d" % i: v for i, v in enumerate(cats)})
    return (" AND ".join(parts) if parts else "1=1"), params


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings()]


def _one(sql: str, params: dict | None = None) -> dict:
    rows = _rows(sql, params)
    return rows[0] if rows else {}


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    resp.headers["x-elapsed-ms"] = "%.1f" % ((time.perf_counter() - t0) * 1000)
    return resp


@app.get("/api/health")
def health():
    with engine.connect() as conn:
        tables = ["fact_sales", "dim_product", "dim_customer", "dim_store", "src_orders"]
        return {t: db.count(conn, t) for t in tables}


@app.get("/api/roles")
def roles():
    return _rows("SELECT role_name, region_scope, category_scope, note FROM dim_role ORDER BY role_name")


@app.get("/api/kpi")
def kpi(role: str | None = None, start: str | None = None, end: str | None = None):
    with engine.connect() as conn:
        where, params = _role(conn, role)
    conds = [where]
    if start:
        conds.append("day >= :start"); params["start"] = start
    if end:
        conds.append("day <= :end"); params["end"] = end
    sql = """
        SELECT COUNT(DISTINCT order_id) AS orders,
               SUM(net_amount) AS gmv,
               SUM(net_amount) / NULLIF(COUNT(DISTINCT order_id), 0) AS avg_order_value,
               SUM(qty) AS units,
               SUM(net_amount) / NULLIF(SUM(qty), 0) AS avg_unit_price,
               SUM(returned_qty) * 1.0 / NULLIF(SUM(qty), 0) AS return_rate,
               SUM(discount_amount) / NULLIF(SUM(gross_amount), 0) AS discount_rate,
               COUNT(DISTINCT customer_key) AS buyers,
               COUNT(DISTINCT product_id) AS active_skus,
               SUM(returned_qty) AS returned_units
          FROM v_sales_wide WHERE %s""" % " AND ".join(conds)
    data = _one(sql, params)
    total_skus = int(_one("SELECT COUNT(*) AS n FROM dim_product")["n"])
    data["sku_count"] = total_skus
    data["sku_coverage"] = (data.get("active_skus") or 0) / total_skus if total_skus else None
    return data


@app.get("/api/trend")
def trend(days: int = Query(30, ge=7, le=400), role: str | None = None):
    with engine.connect() as conn:
        where, params = _role(conn, role)
    sql = """
        SELECT day, COUNT(DISTINCT order_id) AS orders, SUM(net_amount) AS gmv
          FROM v_sales_wide WHERE %s
         GROUP BY day ORDER BY day DESC LIMIT :n""" % where
    params["n"] = days
    return list(reversed(_rows(sql, params)))


@app.get("/api/category")
def category(limit: int = Query(8, ge=1, le=50)):
    return _rows("""
        SELECT category, SUM(gmv) AS gmv, SUM(units) AS units,
               SUM(gmv) / SUM(units) AS avg_price,
               MAX(return_rate) AS worst_return_rate
          FROM v_category_month
         GROUP BY category ORDER BY gmv DESC LIMIT :n""", {"n": limit})


@app.get("/api/region")
def region():
    return _rows("""
        SELECT region, SUM(gmv) AS gmv, SUM(orders) AS orders, SUM(buyers) AS buyers
          FROM v_region_month GROUP BY region ORDER BY gmv DESC""")


@app.get("/api/channel")
def channel():
    return _rows("""
        SELECT channel, channel_group, SUM(gmv) AS gmv, SUM(orders) AS orders
          FROM v_channel_month GROUP BY channel, channel_group ORDER BY gmv DESC""")


@app.get("/api/returns")
def returns():
    return _rows("""
        SELECT reason, SUM(returned_units) AS units, SUM(return_lines) AS return_lines
          FROM v_return_reason_month GROUP BY reason ORDER BY units DESC""")


@app.get("/api/repeat")
def repeat():
    rows = _rows("""
        SELECT sales_month, buyers, repeat_buyers
          FROM v_repeat_month ORDER BY sales_month""")
    for r in rows:
        r["repeat_rate"] = r["repeat_buyers"] / r["buyers"] if r["buyers"] else None
    return rows


@app.get("/api/months")
def months():
    return _rows("""
        SELECT sales_month, orders, gmv, avg_order_value, return_rate, discount_rate,
               buyers, active_skus
          FROM v_kpi_month ORDER BY sales_month""")


@app.get("/api/runs")
def runs(limit: int = Query(10, ge=1, le=100)):
    return _rows("""
        SELECT run_id, started_at, finished_at, mode, rows_in, rows_out, status, note
          FROM etl_run ORDER BY started_at DESC LIMIT :n""", {"n": limit})


@app.get("/api/dq")
def dq(limit: int = Query(12, ge=1, le=100)):
    return _rows("""
        SELECT rule_id, rule_name, target, rows_checked, rows_failed, status, checked_at
          FROM dq_result WHERE run_id = (SELECT MAX(run_id) FROM dq_result)
         -- 按编号排，别让 D10 排在 D2 前面
         ORDER BY LENGTH(rule_id), rule_id LIMIT :n""", {"n": limit})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


WEB = Path(__file__).resolve().parents[2] / "web"
if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


def serve(host: str = "127.0.0.1", port: int = 8010):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    serve()