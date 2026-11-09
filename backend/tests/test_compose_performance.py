"""Настройки производительности в deployment/compose.yml: вес процессора, лимит файлов, метрики LiveKit — и то, что метрики не публикуются наружу.
Разбор текстовый (без PyYAML: его нет в зависимостях backend); полную проверку схемы делает CI командой `docker compose config -q`."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "deployment" / "compose.yml").read_text(encoding="utf-8")


def service(name: str) -> str:
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][\w-]*:\n|^networks:|\Z)", COMPOSE, re.S | re.M)
    assert m, f"в compose нет службы {name}"
    return m.group(1)


def test_livekit_gets_priority_weight_file_limit_and_metrics_without_publishing_them():
    lk = service("livekit")
    assert re.search(r"^    cpu_shares: 8192\b", lk, re.M), "вес процессора звонка выше обычного (1024) и наравне с Egress, это вес, а не лимит"
    assert re.search(r"^    ulimits:\n      nofile:\n        soft: 65536\n        hard: 65536\n", lk, re.M)
    assert re.search(r"^        prometheus_port: 6789\s*$", lk, re.M), "метрики включены"
    ports = re.search(r"^    ports:\n((?:      - .*\n)+)", lk, re.M)
    assert ports, "у LiveKit есть опубликованные порты"
    published = ports.group(1)
    assert "6789" not in published, "порт метрик наружу не публикуется (доступен только внутри сети проекта)"
    assert '127.0.0.1:${LIVEKIT_HTTP_PORT:?}:7880/tcp' in published
    assert not re.search(r"^    cpus:|^    cpuset:|^    mem_limit:", lk, re.M), "никаких жёстких лимитов процессора и памяти звонку"
    assert "6789" not in COMPOSE.replace("prometheus_port: 6789", ""), "порт метрик нигде больше не упоминается (не публикуется, не пробрасывается)"


def test_backend_settings_reach_the_container():
    for name in ("REDIS_MAX_CONNECTIONS", "DB_HOLD_WARN_S", "OUTLOOK_CHECK_CA_FILE", "MEETING_MIX_ENABLED"):
        assert re.search(rf"^      {name}: \$\{{{name}:-", service("backend"), re.M), f"{name} есть в настройках, но в контейнер не передаётся"


def test_host_nginx_does_not_buffer_low_latency_stream():
    for name in ("site.tls.conf.tpl", "site.http.conf.tpl"):
        block = re.search(r"location /llhls/ \{(.*?)\n    \}", (ROOT / "deployment" / "nginx" / name).read_text(encoding="utf-8"), re.S)
        assert block and "proxy_buffering off;" in block.group(1) and "access_log off;" in block.group(1), f"{name}: в пути лежит подпись, а буфер добавляет задержку"


def test_update_counts_its_steps_honestly():
    text = (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")
    always = len(re.findall(r"^  step \"", text, re.M))
    assert f"STEPS={always};" in text and len(re.findall(r"^    step \"", text, re.M)) == 1 and "STEPS=$((STEPS + 1))" in text


@pytest.mark.parametrize("script", ["video.sh", "medianet.sh"])
def test_help_prints_usage_instead_of_treating_it_as_a_command(script):
    r = subprocess.run(["bash", str(ROOT / "scripts" / script), "--help"], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and script in r.stdout, r.stdout + r.stderr


def test_shared_host_does_not_require_buildx_and_is_not_told_to_install_docker(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text('#!/bin/sh\n[ "$1" = buildx ] && exit 1\nexit 0\n', encoding="utf-8")
    fake.chmod(0o755)
    lib = ROOT / "scripts" / "lib" / "prereqlib.sh"
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin"}
    out = {p: subprocess.run(["bash", "-c", f'source "{lib}"; prereq_verify {p}'], capture_output=True, text=True, env=env, timeout=20).stdout for p in ("standalone", "shared-host")}
    assert "buildx" in out["standalone"] and "buildx" not in out["shared-host"]
    assert "ставит его администратор" in (ROOT / "scripts" / "prereq.sh").read_text(encoding="utf-8")


def test_other_services_get_lower_weights_but_no_hard_limits():
    assert re.search(r"^    cpu_shares: 512\b", service("asr"), re.M)
    for name in ("asr", "backend", "web", "postgres", "redis"):
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


def test_egress_healthcheck_never_cleans_the_service_socket_and_notices_when_it_is_gone():
    """Уборка в проверке здоровья Egress удаляла файлы старше 2900 минут во всём /live/tmp — в том числе сокет IPC службы (NE_<узел>/service_ipc.sock):
    через 48 ч 20 мин после запуска каждый новый эфир падал с «Служба трансляций недоступна» до перезапуска Egress. Чистить можно только каталоги заданий EGH_*."""
    egress = service("livekit-egress")
    m = re.search(r'test: \["CMD-SHELL", "(.*)"\]', egress)
    assert m, "у Egress есть проверка здоровья"
    cmd = m.group(1)
    for part in re.findall(r"find /live/tmp[^;]*", cmd):
        if "-delete" in part:
            assert "EGH_" in part, f"уборка должна быть ограничена каталогами заданий: {part}"
    assert "NE_" not in " ".join(p for p in re.findall(r"find /live/tmp[^;]*", cmd) if "-delete" in p), "каталог узла NE_* трогать нельзя"
    assert "/live/tmp/NE_*/service_ipc.sock" in cmd and "|| exit 1" in cmd, "пропавший сокет должен делать службу unhealthy"
