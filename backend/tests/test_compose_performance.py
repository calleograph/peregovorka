"""Настройки производительности в deployment/compose.yml: вес процессора, лимит файлов, метрики LiveKit — и то, что метрики не публикуются наружу.
Разбор текстовый (без PyYAML: его нет в зависимостях backend); полную проверку схемы делает CI командой `docker compose config -q`."""
from __future__ import annotations

import re
from pathlib import Path

COMPOSE = (Path(__file__).resolve().parents[2] / "deployment" / "compose.yml").read_text(encoding="utf-8")


def service(name: str) -> str:
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][\w-]*:\n|^networks:|\Z)", COMPOSE, re.S | re.M)
    assert m, f"в compose нет службы {name}"
    return m.group(1)


def test_livekit_gets_priority_weight_file_limit_and_metrics_without_publishing_them():
    lk = service("livekit")
    assert re.search(r"^    cpu_shares: 2048\b", lk, re.M), "вес процессора звонка выше обычного (1024), это вес, а не лимит"
    assert re.search(r"^    ulimits:\n      nofile:\n        soft: 65536\n        hard: 65536\n", lk, re.M)
    assert re.search(r"^        prometheus_port: 6789\s*$", lk, re.M), "метрики включены"
    ports = re.search(r"^    ports:\n((?:      - .*\n)+)", lk, re.M)
    assert ports, "у LiveKit есть опубликованные порты"
    published = ports.group(1)
    assert "6789" not in published, "порт метрик наружу не публикуется (доступен только внутри сети проекта)"
    assert '127.0.0.1:${LIVEKIT_HTTP_PORT:?}:7880/tcp' in published
    assert not re.search(r"^    cpus:|^    cpuset:|^    mem_limit:", lk, re.M), "никаких жёстких лимитов процессора и памяти звонку"
    assert "6789" not in COMPOSE.replace("prometheus_port: 6789", ""), "порт метрик нигде больше не упоминается (не публикуется, не пробрасывается)"


def test_other_services_get_lower_weights_but_no_hard_limits():
    assert re.search(r"^    cpu_shares: 512\b", service("asr"), re.M)
    assert re.search(r"^    cpu_shares: 256\b", service("llm-local"), re.M)
    for name in ("asr", "llm-local", "backend", "web", "postgres", "redis"):
        assert not re.search(r"^    cpus:|^    cpuset:", service(name), re.M), f"{name}: жёсткого лимита процессора нет"


def test_backend_passes_the_heavy_task_settings_with_safe_defaults():
    be = service("backend")
    assert "HEAVY_CONCURRENCY: ${HEAVY_CONCURRENCY:-2}" in be and "HEAVY_MAX_DEFER_SECONDS: ${HEAVY_MAX_DEFER_SECONDS:-1800}" in be
    assert "LIVEKIT_METRICS_URL: ${LIVEKIT_METRICS_URL:-}" in be, "пустое значение — адрес выводится из LIVEKIT_INTERNAL_URL (внутренняя сеть)"


def test_metrics_url_is_derived_inside_the_project_network_only():
    from app.config import Settings
    from app.services.perf import metrics_url

    s = Settings(livekit_internal_url="ws://livekit:7880", livekit_api_key="k", livekit_api_secret="s" * 40)
    assert metrics_url(s) == "http://livekit:6789/metrics", "внутреннее имя службы звонков, не публичный адрес"
    s2 = Settings(livekit_internal_url="ws://livekit:7880", livekit_public_url="wss://meet.example.com/livekit", livekit_api_key="k", livekit_api_secret="s" * 40)
    assert "example.com" not in metrics_url(s2)
    s3 = Settings(livekit_internal_url="ws://livekit:7880", livekit_metrics_url="http://lk-metrics:9999/metrics", livekit_api_key="k", livekit_api_secret="s" * 40)
    assert metrics_url(s3) == "http://lk-metrics:9999/metrics"
