# -*- coding: utf-8 -*-
"""配置加载：优先环境变量，其次 config.yml，最后是本地 SQLite 兜底。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    url: str = "sqlite:///./warehouse.db"
    source_scale: float = 1.0
    start_date: str = "2025-01-01"
    end_date: str = "2026-11-10"
    seed: int = 20261113
    mail: dict = field(default_factory=dict)

    @property
    def is_mysql(self) -> bool:
        return self.url.startswith("mysql")


def load(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    p = Path(path or os.environ.get("BIDATA_CONFIG", "config.yml"))
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        db = raw.get("database", {}) or {}
        gen = raw.get("generate", {}) or {}
        if db.get("url"):
            cfg.url = db["url"]
        if gen.get("scale") is not None:
            cfg.source_scale = float(gen["scale"])
        cfg.start_date = gen.get("start_date", cfg.start_date)
        cfg.end_date = gen.get("end_date", cfg.end_date)
        cfg.seed = int(gen.get("seed", cfg.seed))
        cfg.mail = raw.get("mail", {}) or {}
    if os.environ.get("BIDATA_URL"):
        cfg.url = os.environ["BIDATA_URL"]
    return cfg
