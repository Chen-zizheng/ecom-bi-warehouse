# -*- coding: utf-8 -*-


def test_health(client):
    body = client.get("/api/health").json()
    assert body["fact_sales"] > 0
    assert body["dim_product"] > 0


def test_kpi_default(client):
    body = client.get("/api/kpi").json()
    assert body["orders"] > 0
    assert body["gmv"] > 0
    assert 0 <= body["return_rate"] <= 1
    assert 0 < body["sku_coverage"] <= 1


def test_role_filters_rows(client):
    total = client.get("/api/kpi").json()
    east = client.get("/api/kpi", params={"role": "east_ops"}).json()
    apparel = client.get("/api/kpi", params={"role": "apparel_pm"}).json()
    assert 0 < east["gmv"] < total["gmv"]
    assert 0 < apparel["gmv"] < total["gmv"]


def test_unknown_role_is_403(client):
    r = client.get("/api/kpi", params={"role": "nobody"})
    assert r.status_code == 403
    assert "unknown role" in r.json()["detail"]


def test_trend_and_dimensions(client):
    trend = client.get("/api/trend", params={"days": 7}).json()
    assert 1 <= len(trend) <= 7
    assert client.get("/api/category").json()
    assert client.get("/api/region").json()
    assert client.get("/api/returns").json()
    assert client.get("/api/channel").json()


def test_repeat_rate_between_0_and_1(client):
    for row in client.get("/api/repeat").json():
        assert 0 <= row["repeat_rate"] <= 1


def test_dq_endpoint_returns_latest_run(client):
    rows = client.get("/api/dq").json()
    assert len(rows) == 12
    assert {r["status"] for r in rows} <= {"PASS", "FAIL", "ERROR"}
    assert client.get("/api/runs").json()[0]["run_id"].startswith("ETL")
