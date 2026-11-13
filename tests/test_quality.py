# -*- coding: utf-8 -*-
from sqlalchemy import text


def test_twelve_rules_and_all_pass(ctx):
    rows = ctx["dq"]
    assert len(rows) == 12
    bad = [r for r in rows if r["status"] != "PASS"]
    assert bad == [], bad


def test_rule_results_persisted(ctx):
    engine = ctx["engine"]
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM dq_result")).scalar()
        latest = conn.execute(text("SELECT COUNT(*) FROM dq_result "
                                   "WHERE run_id = (SELECT MAX(run_id) FROM dq_result)")).scalar()
    assert n >= 12
    assert latest == 12


def test_reject_rows_are_explained(ctx):
    engine = ctx["engine"]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT rule_id, reason FROM etl_reject")).all()
    assert rows
    for rule_id, reason in rows:
        assert rule_id.startswith("R")
        assert reason
