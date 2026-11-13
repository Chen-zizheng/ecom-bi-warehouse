# -*- coding: utf-8 -*-
"""测试用 SQLite：一个 1200 单的缩小数据集，2 秒内跑完。

被测的是装载、质量规则、视图与接口四层，跟具体数据库无关；
真机 MySQL 上那组数字写在 README 里，这里只保证逻辑对。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from bidata import cli, config as cfgmod, db, etl, generate, metrics, quality  # noqa: E402


@pytest.fixture(scope="session")
def ctx(tmp_path_factory):
    d = tmp_path_factory.mktemp("bidata")
    cfg = cfgmod.Config(url="sqlite:///" + (d / "test.db").as_posix(),
                        source_scale=0.004, seed=7,
                        start_date="2025-01-01", end_date="2025-03-31")
    engine = db.make_engine(cfg.url)
    db.create_all(engine)
    with db.session(engine) as conn:
        cli.seed_roles(conn)
    generate.build(cfg)
    full = etl.run(engine, cfg, mode="full")
    dq = quality.run(engine, cfg)
    metrics.build(engine, cfg)
    os.environ["BIDATA_URL"] = cfg.url   # api 模块导入时读这个
    return {"cfg": cfg, "engine": engine, "full": full, "dq": dq}


@pytest.fixture(scope="session")
def client(ctx):
    from fastapi.testclient import TestClient
    from bidata.api import app
    return TestClient(app)
