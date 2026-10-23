"""Пулы соединений всех процессов backend помещаются в max_connections PostgreSQL."""
from __future__ import annotations

import os

from app.db import PG_RESERVED, pool_plan

from .conftest import make_settings


def _total(s) -> int:
    a, b = pool_plan(s, background=False), pool_plan(s, background=True)
    return (a["pool_size"] + a["max_overflow"] + b["pool_size"] + b["max_overflow"] + 2) * s.uvicorn_workers + PG_RESERVED


def test_auto_pools_shrink_to_fit_postgres_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 16)              # 16 ядер: по умолчанию 40+40 и 12+12 на процесс
    big = make_settings(tmp_path, postgres_max_connections=300, uvicorn_workers=4)
    assert _total(big) <= 300 and pool_plan(big, background=False)["note"]
    one = make_settings(tmp_path, postgres_max_connections=300, uvicorn_workers=1)
    assert pool_plan(one, background=False) == {"pool_size": 40, "max_overflow": 40, "note": None}, "помещается — размеры прежние"
    unknown = make_settings(tmp_path, uvicorn_workers=8)
    assert pool_plan(unknown, background=False)["pool_size"] == 40, "предел неизвестен — не ужимаем"
    explicit = make_settings(tmp_path, postgres_max_connections=100, uvicorn_workers=4, db_pool_size=30)
    p = pool_plan(explicit, background=False)
    assert p["pool_size"] == 30 and "увеличьте POSTGRES_MAX_CONNECTIONS" in p["note"], "явные значения не меняем, но предупреждаем"
