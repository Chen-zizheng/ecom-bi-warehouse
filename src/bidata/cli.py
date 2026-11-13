# -*- coding: utf-8 -*-
"""命令行入口：python -m bidata.cli <命令>。

把造数、装载、质检、建视图串成一条线，是为了让别人 clone 之后不用读源码就会跑：
四条命令对应四件事，出错能停在哪一步一目了然。
"""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import text

from . import config as cfgmod
from . import db, etl, generate, metrics, quality

ROLES = [
    ("analyst", None, None, "全量"),
    ("east_ops", "华东", None, "只看华东大区"),
    ("apparel_pm", None, "服饰", "只看服饰品类"),
]


def seed_roles(conn):
    rows = [dict(role_name=r, region_scope=g, category_scope=c, note=n) for r, g, c, n in ROLES]
    return db.upsert(conn, db.schema.dim_role, rows, ["role_name"])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bidata", description="电商 BI 数据管道")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="造源数据")
    g.add_argument("--scale", type=float, default=None)
    g.add_argument("--append-orders", type=int, default=0)
    e = sub.add_parser("etl", help="装载维表与事实表")
    e.add_argument("--mode", choices=["full", "incr"], default="incr")
    sub.add_parser("dq", help="跑 12 条质量规则")
    sub.add_parser("views", help="重建指标视图")
    sub.add_parser("all", help="gen + etl + dq + views")
    sub.add_parser("serve", help="启动报表服务")
    a = ap.parse_args(argv)

    cfg = cfgmod.load(a.config)
    engine = db.make_engine(cfg.url)
    db.create_all(engine)
    with db.session(engine) as conn:
        seed_roles(conn)

    t0 = time.perf_counter()
    if a.cmd == "gen":
        if a.scale:
            cfg.source_scale = a.scale
        w = generate.build(cfg, append_orders=a.append_orders)
        for k, v in w.items():
            print("%-18s %8d" % (k, v))
    elif a.cmd == "etl":
        s = etl.run(engine, cfg, mode=a.mode)
        print("rows_in=%d rows_out=%d rejected=%d %.2fs"
              % (s["rows_in"], s["rows_out"], s["rejected"], s["seconds"]))
        if s.get("reject_rules"):
            print("reject by rule:", s["reject_rules"])
    elif a.cmd == "dq":
        rows = quality.run(engine, cfg)
        for r in rows:
            print("%-4s %-30s checked=%-8d failed=%-6d %s"
                  % (r["rule_id"], r["rule_name"], r["rows_checked"], r["rows_failed"], r["status"]))
        bad = [r for r in rows if r["status"] != "PASS"]
        print("%d rules, %d failed, %.2fs" % (len(rows), len(bad), rows[0]["seconds"]))
        if bad:
            return 1
    elif a.cmd == "views":
        print("views:", len(metrics.build(engine, cfg)))
    elif a.cmd == "all":
        generate.build(cfg)
        s = etl.run(engine, cfg, mode="full")
        print("etl rows_out=%d rejected=%d %.2fs" % (s["rows_out"], s["rejected"], s["seconds"]))
        rows = quality.run(engine, cfg)
        print("dq %d rules, %d failed" % (len(rows), sum(1 for r in rows if r["status"] != "PASS")))
        print("views:", len(metrics.build(engine, cfg)))
    elif a.cmd == "serve":
        from .api import serve
        serve()
        return 0
    print("total %.2fs" % (time.perf_counter() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
