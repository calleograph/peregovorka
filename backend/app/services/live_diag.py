"""Диагностика эфира: сбор показателей сервера и цепочки «источник → LiveKit → Egress → HLS/RTMP → зритель», выявление узких мест, журнал и итог эфира.

Устройство:
  * собирает один ведущий экземпляр и только пока идёт эфир: чтение /proc — раз в 2 с, службы и файлы HLS — раз в 6 с, разбор одного сегмента ffprobe — раз в 30 с;
    подключение страниц диагностики на сбор не влияет, их опрос читает готовый снимок из Redis;
  * текущий снимок и ряд для графиков — Redis (живут минуты); журнал и показатели раз в 15 с — база: переживают перезапуск контейнеров и сервера;
  * показатель, который получить нельзя, — `None`: интерфейс показывает «нет данных», а не ноль;
  * сбор не может повлиять на эфир: все обращения с таймаутами, любая ошибка опроса — только пропуск показателя;
  * в журнал и выгрузки не попадают ключи RTMP, токены, адреса с подписями, пароли и переписка: всё проходит `clean` (маскирование по имени поля и по виду значения).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import statistics
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, func, select, update

from ..models import Broadcast, LiveDestination, LiveDiagEvent, LiveDiagSample, Meeting, Room
from .diagnostics import mask_report
from .live import LIVE_ACTIVE, LIVE_QUALITY
from .live_destinations import PLATFORMS
from .perf import RateTracker, parse_prometheus, read_loadavg, read_meminfo

log = logging.getLogger("app.live_diag")

TICK_S = 2.0                  # чтение /proc
SLOW_TICKS = 3                # службы, HLS, зрители — каждый третий такт (6 с)
PERSIST_S = 15.0              # показатели в базу
PROBE_S = 30.0                # разбор сегмента ffprobe
IDLE_S = 10.0                 # проверка «начался ли эфир», пока эфиров нет
RING_POINTS = 300             # точек ряда в Redis: 30 минут при шаге 6 с
TTL_NOW_S = 60
CLIENT_MAX_AGE_S = 75         # отчёт зрителя свежий: пульс раз в 30 с плюс запас
CLIENTS_MAX = 5000            # отчётов на эфир в Redis
MAX_EVENTS = 20_000           # событий на эфир: дальше пишутся только предупреждения и ошибки
MAX_SAMPLE_ROWS = 120_000     # показателей всего (≈ 500 МБ: замер — 4 КБ на строку вместе с индексом, 20 суток непрерывных эфиров): сверх этого удаляются самые старые
MIN_FREE_BYTES = 1024**3      # меньше 1 ГБ на томе: показатели в базу не пишем, чтобы диагностика не усугубляла нехватку места
RETENTION_EVERY_S = 3600.0
HOST_NETDEV = "/host/netdev"  # счётчики сетевых интерфейсов хоста: файл хоста подключён только для чтения (deployment/compose.yml)
EGRESS_CPU_FILE = "diag/egress.cpu"     # cpu.stat контейнера Egress: его пишет проверка здоровья службы (без сокета Docker приложение чужих контейнеров не видит)

K_NOW, K_RING, K_CLIENTS = "ld:{}:now", "ld:{}:ring", "ld:{}:cl"
SKIP_IF = re.compile(r"^(lo|veth|docker|br-|virbr|cni|flannel|cali|tun|tap|wg)")
LEVEL_NAME = {0: "info", 1: "warn", 2: "error"}
_RTMP = re.compile(r"rtmps?://\S+", re.I)
_SECRET = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")          # идентификаторы эфиров нужны в выгрузке, это не секрет
_HLS_PATH = re.compile(r"/(?:live/b|hls|acc)/[0-9a-f]{16,}\S*")


def clean_text(text: Any, limit: int = 390) -> str:
    """Текст для журнала: без адресов RTMP (в них ключ), путей с подписями и длинных токенов."""
    t = _RTMP.sub("rtmp://***", str(text or ""))
    t = _HLS_PATH.sub("/***", t)
    return _SECRET.sub(lambda m: m.group(0) if _UUID.fullmatch(m.group(0)) else "***", t)[:limit]


def clean(value: Any) -> Any:
    """Подробности события и выгрузки: маскирование секретов по имени поля и по виду значения, затем адреса и токены в строках."""
    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return clean_text(v, 600) if isinstance(v, str) else v
    return walk(mask_report(value))


# ------------------------------------------------------------------------------------------------ чтение хоста (только /proc; микросекунды)
def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def read_cores(root: str = "/proc") -> dict[str, tuple[int, int]]:
    """{"cpu": (занято, всего), "cpu0": …} — общий счётчик и по ядрам."""
    out: dict[str, tuple[int, int]] = {}
    for line in (_read(f"{root}/stat") or "").splitlines():
        if not line.startswith("cpu"):
            break
        p = line.split()
        try:
            v = [int(x) for x in p[1:9]]
        except ValueError:
            continue
        total = sum(v)
        out[p[0]] = (total - v[3] - v[4], total)
    return out


def read_disk(dev: tuple[int, int] | None, root: str = "/proc") -> dict[str, int] | None:
    """Счётчики устройства из /proc/diskstats: чтения и записи (операции, секторы, мс) и время занятости."""
    if dev is None:
        return None
    for line in (_read(f"{root}/diskstats") or "").splitlines():
        p = line.split()
        if len(p) >= 14 and (int(p[0]), int(p[1])) == dev:
            return {"r": int(p[3]), "rs": int(p[5]), "rms": int(p[6]), "w": int(p[7]), "ws": int(p[9]), "wms": int(p[10]), "busy": int(p[12])}
    return None


def read_netdev(paths: tuple[str, ...] = (HOST_NETDEV, "/proc/net/dev")) -> tuple[str, dict[str, list[int]]]:
    """(откуда: host | container, {интерфейс: [rx байт, rx ошибок, rx отброшено, tx байт, tx ошибок, tx отброшено]}). Без внутренних интерфейсов Docker."""
    for i, path in enumerate(paths):
        txt = _read(path)
        if not txt or ":" not in txt:
            continue
        out: dict[str, list[int]] = {}
        for line in txt.splitlines()[2:]:
            name, _, rest = line.partition(":")
            name = name.strip()
            p = rest.split()
            if len(p) >= 12 and not SKIP_IF.match(name):
                out[name] = [int(p[0]), int(p[2]), int(p[3]), int(p[8]), int(p[10]), int(p[11])]
        if out:
            return ("host" if i == 0 and len(paths) > 1 else "container"), out
    return "none", {}


def device_of(path: Path) -> tuple[int, int] | None:
    try:
        st = os.stat(path if path.exists() else path.parent)
    except OSError:
        return None
    return os.major(st.st_dev), os.minor(st.st_dev)


# ------------------------------------------------------------------------------------------------ HLS: плейлист и файлы сегментов
def parse_playlist(text: str) -> dict:
    """Живой плейлист Egress: номер первого сегмента, для каждого — длительность, момент начала, признак разрыва. Метка времени стоит не у каждого сегмента: недостающие — от предыдущего."""
    first, ended, items = 0, False, []
    dur, pdt, disc, prev_end = None, None, False, None
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            first = int(line.split(":")[1] or 0)
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                pdt = datetime.fromisoformat(line.split(":", 1)[1].strip().replace("Z", "+00:00")).timestamp()
            except ValueError:
                pdt = None
        elif line.startswith("#EXTINF:"):
            try:
                dur = float(line[8:].split(",")[0])
            except ValueError:
                dur = None
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            disc = True
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif line and not line.startswith("#"):
            start = pdt if pdt is not None else prev_end
            items.append({"seq": first + len(items), "name": line.strip(), "dur": dur or 0.0, "pdt": start, "disc": disc})
            prev_end = start + (dur or 0.0) if start is not None else None
            dur, pdt, disc = None, None, False
    return {"first": first, "ended": ended, "items": items}


def hls_stats(path: Path, now: float | None = None) -> dict | None:
    """Показатели формирования HLS по файлам. Одно чтение плейлиста и несколько stat — без разбора видео."""
    now = time.time() if now is None else now
    txt = _read(str(path / "index.m3u8"))
    if txt is None:
        return None
    pl = parse_playlist(txt)
    tail = pl["items"][-6:]
    files = []
    for it in tail:
        try:
            st = os.stat(path / it["name"])
        except OSError:
            continue
        files.append((it, st.st_mtime, st.st_size))
    if not files:
        return {"count": len(pl["items"]), "age_s": None, "ended": pl["ended"], "seq": None}
    last, mtime, size = files[-1]
    durs = [f[0]["dur"] for f in files if f[0]["dur"]]
    gaps = [b[1] - a[1] for a, b in zip(files, files[1:])]
    total_d = sum(durs)
    ready = None
    if last["pdt"] is not None:
        ready = round(mtime - (last["pdt"] + last["dur"]), 2)
    return {"count": len(pl["items"]), "seq": last["seq"], "name": last["name"], "ended": pl["ended"], "age_s": round(now - mtime, 2), "dur_s": round(total_d / len(durs), 3) if durs else None,
            "interval_s": round(sum(gaps) / len(gaps), 3) if gaps else None, "interval_max_s": round(max(gaps), 3) if gaps else None, "bytes": size,
            "kbps": round(sum(f[2] for f in files) * 8 / total_d / 1000) if total_d else None, "ready_s": ready, "disc": sum(1 for f in files if f[0]["disc"])}


def parse_probe(raw: dict) -> dict | None:
    """Разбор ffprobe одного сегмента: разрешение, кадры, битрейт дорожек, пропуски кадров, сдвиг звука относительно видео в начале сегмента."""
    streams = raw.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    pv, pa = [], []
    for p in raw.get("packets") or []:
        try:
            item = (float(p["pts_time"]), int(p.get("size") or 0), "K" in str(p.get("flags") or ""))
        except (KeyError, TypeError, ValueError):
            continue
        (pv if video is not None and p.get("stream_index") == video.get("index") else pa).append(item)
    if not pv:
        return None
    pv.sort()
    pa.sort()
    deltas = [b[0] - a[0] for a, b in zip(pv, pv[1:])]
    step = statistics.median(deltas) if deltas else 0.0
    dur = (pv[-1][0] - pv[0][0]) + step
    out: dict[str, Any] = {"res": f"{video.get('width')}x{video.get('height')}", "vcodec": video.get("codec_name"), "acodec": (audio or {}).get("codec_name"),
                           "frames": len(pv), "keyframes": sum(1 for x in pv if x[2]), "gaps": sum(1 for d in deltas if step and d > step * 1.75)}
    if dur > 0:
        out["fps"] = round(len(pv) / dur, 2)
        out["vkbps"] = round(sum(x[1] for x in pv) * 8 / dur / 1000)
    if pa:
        adur = pa[-1][0] - pa[0][0]
        out["akbps"] = round(sum(x[1] for x in pa) * 8 / adur / 1000) if adur > 0 else None
        out["av_ms"] = round((pa[0][0] - pv[0][0]) * 1000)
    return out


async def probe_segment(path: Path, timeout: float = 5.0) -> dict | None:
    """ffprobe с пониженным приоритетом и таймаутом. Нет ffprobe или файл уже удалён — просто нет показателя."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nice", "-n", "15", "ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name,width,height:packet=stream_index,pts_time,size,flags", "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    try:
        return parse_probe(json.loads(out)) if proc.returncode == 0 else None
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------ отчёты клиентов
_CLIENT_NUM = {"kbps": 200_000, "est": 2_000_000, "fps": 240, "drop": 1e9, "frames": 1e10, "buf": 3600, "lag": 86400, "lat": 86400, "seg_ms": 600_000, "seg_kb": 200_000, "stalls": 1e7, "stall_ms": 1e11,
               "errs": 1e7, "reconn": 1e6, "rtt": 60_000, "loss": 100, "out_kbps": 500_000, "in_kbps": 500_000, "jitter": 60_000}
_CLIENT_STR = {"res": re.compile(r"^\d{1,5}x\d{1,5}$"), "eng": re.compile(r"^(hlsjs|native|webrtc)$"), "limit": re.compile(r"^(none|cpu|bandwidth|other)$"), "src": re.compile(r"^(screen|camera)$"),
               "node": re.compile(r"^[A-Za-z0-9.-]{1,253}$")}


def clean_client(raw: Any) -> dict | None:
    """Отчёт браузера — недоверенные данные: только известные поля, числа в разумных пределах и короткие значения по образцу."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for k, cap in _CLIENT_NUM.items():
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= cap:
            out[k] = round(float(v), 2)
    for k, rx in _CLIENT_STR.items():
        v = raw.get(k)
        if isinstance(v, str) and rx.match(v):
            out[k] = v
    return out or None


def viewer_verdict(c: dict, prev: dict | None, seg_s: float, need_kbps: float) -> tuple[int, str]:
    """Состояние зрителя по его отчёту: (0 норма | 1 хуже | 2 плохо, причина). Счётчики — разность с прошлым отчётом этого зрителя."""
    p = prev or {}
    stalls = max(0.0, c.get("stalls", 0) - p.get("stalls", c.get("stalls", 0)))
    errs = max(0.0, c.get("errs", 0) - p.get("errs", c.get("errs", 0)))
    reconn = max(0.0, c.get("reconn", 0) - p.get("reconn", c.get("reconn", 0)))
    stood = max(0.0, c.get("stall_ms", 0) - p.get("stall_ms", c.get("stall_ms", 0)))
    slow = c.get("seg_ms") is not None and seg_s > 0 and c["seg_ms"] > seg_s * 800
    thin = c.get("est") is not None and need_kbps > 0 and c["est"] < need_kbps * 1.1
    if errs or reconn:
        return 2, "ошибки воспроизведения или переподключения"
    if stood >= 3000:                     # одна длинная остановка — это один счётчик, но зритель всё это время не видит эфир
        return 2, f"плеер стоит {stood / 1000:.0f} с: данные не приходят"
    if stalls >= 2:
        return 2, "плеер теряет буфер" + (": канал зрителя ниже битрейта" if slow or thin else "")
    if stalls or slow:
        return 1, "канал зрителя на пределе" if slow or thin else "остановка воспроизведения"
    if c.get("lag", 0) > 15:
        return 1, f"отставание {c['lag']:.0f} с"
    return 0, ""


def aggregate_clients(rows: dict[str, dict], prev: dict[str, dict], seg_s: float, need_kbps: float, now: float, memo: dict[str, tuple] | None = None) -> dict:
    """Сводка по отчётам зрителей: сколько отчитались, средние, доля зрителей с проблемой, худшие (без имён: короткий номер и браузер).
    Сводка считается раз в 6 с, а зритель отчитывается раз в 30 с: вывод о зрителе держится до его следующего отчёта (`memo`), иначе проблема жила бы один опрос."""
    memo = {} if memo is None else memo
    fresh = {k: v for k, v in rows.items() if now - float(v.get("t", 0)) < CLIENT_MAX_AGE_S}
    viewers = {k: v for k, v in fresh.items() if k.startswith("v:")}
    pubs = {k: v for k, v in fresh.items() if k.startswith("p:")}

    def nums(key: str) -> list[float]:
        return sorted(float(v[key]) for v in viewers.values() if isinstance(v.get(key), (int, float)))

    def avg(key: str) -> float | None:
        xs = nums(key)
        return round(sum(xs) / len(xs), 2) if xs else None

    def p95(key: str) -> float | None:
        xs = nums(key)
        return round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 2) if xs else None

    bad, stalls, reconn, errs = [], 0.0, 0.0, 0.0
    for k, v in viewers.items():
        p = prev.get(k) or {}
        stalls += max(0.0, v.get("stalls", 0) - p.get("stalls", v.get("stalls", 0)))
        reconn += max(0.0, v.get("reconn", 0) - p.get("reconn", v.get("reconn", 0)))
        errs += max(0.0, v.get("errs", 0) - p.get("errs", v.get("errs", 0)))
        known = memo.get(k)
        if known is None or known[0] != v.get("t"):
            known = memo[k] = (v.get("t"), *viewer_verdict(v, p, seg_s, need_kbps))
        _t, lv, why = known
        if lv:
            bad.append({"id": k[2:8], "lv": lv, "why": why, "ua": v.get("ua"), "seg_ms": v.get("seg_ms"), "est": v.get("est"), "buf": v.get("buf"), "lag": v.get("lag"), "node": v.get("node")})
    for k in memo.keys() - viewers.keys():
        del memo[k]
    bad.sort(key=lambda x: -x["lv"])
    return {"reporting": len(viewers), "bad": len(bad), "bad_share": round(len(bad) / len(viewers), 3) if viewers else None, "worst": bad[:8], "stalls": int(stalls), "reconn": int(reconn), "errs": int(errs),
            "kbps": avg("kbps"), "est": avg("est"), "buf": avg("buf"), "lat": avg("lat"), "lat_p95": p95("lat"), "lag": avg("lag"), "seg_ms": avg("seg_ms"), "seg_ms_p95": p95("seg_ms"), "fps": avg("fps"),
            "publishers": [{"src": v.get("src"), "res": v.get("res"), "fps": v.get("fps"), "kbps": v.get("out_kbps"), "rtt": v.get("rtt"), "loss": v.get("loss"), "limit": v.get("limit")} for v in pubs.values()][:8]}


# ------------------------------------------------------------------------------------------------ правила: что подозрительно и почему
@dataclass
class Finding:
    code: str
    level: int                     # 1 — предупреждение, 2 — критично
    component: str
    stage: str                     # source | livekit | egress | output | delivery | server
    text: str
    cause: str = ""
    kind: str = "unknown"          # confirmed | assumed | unknown


NEED_MORE = "Данных недостаточно: нужна дополнительная диагностика."
CLEAR_POLLS, CLEAR_VIEWER = 3, 7     # сколько чистых опросов подряд (по 6 с) закрывают находку


def d1(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def hurts_viewers(stage: str, component: str, level: int) -> bool:
    """Может ли находка объяснить проблемы зрителей HLS: сбой формирования потока или серьёзный сбой сервера. Отказ площадки RTMP и сами зрители — нет."""
    return component != "rtmp" and (stage in ("output", "egress") or (level == 2 and stage != "delivery"))


def rules(s: dict, cfg: dict) -> list[Finding]:
    """Состояние снимка → находки. Подтверждённая причина — только когда её показывает измерение; иначе это предположение или прямо «данных недостаточно»."""
    out: list[Finding] = []
    host, svc = s.get("host") or {}, s.get("svc") or {}
    eg, lk = svc.get("egress") or {}, svc.get("livekit") or {}
    hls, media, view = s.get("hls") or {}, s.get("media") or {}, s.get("viewers") or {}
    load, cores = eg.get("load"), eg.get("cores")
    cpu = host.get("cpu")
    ncpu = host.get("cpus") or 1
    max_load = cfg.get("max_cpu_load", 85)
    eg_share = (cores / ncpu * 100) if cores is not None else None

    if cpu is not None and cpu >= 85:
        if eg_share is not None and cpu and eg_share / cpu >= 0.5:
            c, k = f"Основную нагрузку даёт Egress: {d1(cores)} яд. из {ncpu}.", "confirmed"
        elif eg_share is not None:
            c, k = f"Egress занимает {d1(cores)} яд. из {ncpu}: нагрузку создаёт другая служба или программа на сервере.", "assumed"
        else:
            c, k = NEED_MORE, "unknown"
        out.append(Finding("cpu_high", 2 if cpu >= 95 else 1, "server", "server", f"Процессор сервера занят на {cpu:.0f} %", c, k))
    mem = (host.get("mem") or {}).get("avail_pct")
    if mem is not None and mem < 10:
        out.append(Finding("mem_low", 2 if mem < 5 else 1, "server", "server", f"Осталось {mem:.0f} % памяти", NEED_MORE))
    disk = host.get("disk") or {}
    free = disk.get("free")
    if free is not None:
        floor = cfg.get("min_free_gb", 10) * 1024**3
        if free < floor * 2:
            out.append(Finding("disk_low", 2 if free < floor else 1, "server", "server", f"На томе эфира свободно {d1(free / 1024**3)} ГБ", f"Порог запуска эфира — {cfg.get('min_free_gb', 10)} ГБ.", "confirmed"))
    if ((disk.get("util") or 0) >= 90 and (disk.get("await_ms") or 0) >= 50) or (disk.get("await_ms") or 0) >= 200:      # у быстрых дисков занятость 100 % ещё не упор: нужна и задержка
        wr = (disk.get("wr_bps") or 0) / 1e6
        out.append(Finding("disk_busy", 1, "server", "server", f"Диск перегружен: занят {disk.get('util') or 0:.0f} %, задержка {disk.get('await_ms') or 0:.0f} мс", f"Запись {wr:.0f} МБ/с. Сегменты HLS и запись MP4 идут на этот том.", "assumed"))
    net = host.get("net") or {}
    cap = net.get("cap_bps")
    if cap and net.get("tx_bps") is not None and net["tx_bps"] >= cap * 0.9:
        out.append(Finding("net_saturated", 2 if net["tx_bps"] >= cap * 0.98 else 1, "server", "delivery", f"Исходящий трафик {net['tx_bps'] / 1e6:.0f} Мбит/с из {cap / 1e6:.0f}", "Канал сервера упирается в заданную пропускную способность.", "confirmed"))
    if (net.get("err_s") or 0) > 0 or (net.get("drop_s") or 0) > 1:
        out.append(Finding("net_errors", 1, "server", "delivery", f"Сетевой интерфейс: ошибок {d1(net.get('err_s') or 0)}/с, отброшено {d1(net.get('drop_s') or 0)}/с", "Ошибки считает интерфейс сервера.", "confirmed"))

    for name, title in (("redis", "Redis"), ("postgres", "База данных"), ("nginx", "nginx"), ("backend", "Приложение")):
        v = svc.get(name) or {}
        if v.get("st") == "down":
            out.append(Finding(f"{name}_down", 2, name, "server", f"{title} не отвечает", v.get("err") or NEED_MORE, "confirmed"))
        elif (v.get("ms") or 0) >= 500:
            out.append(Finding(f"{name}_slow", 1, name, "server", f"{title} отвечает {v['ms']:.0f} мс", NEED_MORE))
    if lk.get("st") == "down":
        out.append(Finding("livekit_down", 2, "livekit", "livekit", "LiveKit не отвечает", lk.get("err") or NEED_MORE, "confirmed"))
    for key, stage, who in (("loss_in", "source", "от выступающих к серверу"), ("loss_out", "livekit", "от сервера к участникам")):
        v = lk.get(key)
        if v is not None and v >= 3:
            out.append(Finding(f"lk_{key}", 2 if v >= 10 else 1, "livekit", stage, f"Потери пакетов {who}: {d1(v)} %", "Сеть между участником и сервером.", "assumed"))

    if eg.get("st") == "down":
        out.append(Finding("egress_down", 2, "egress", "egress", "Egress не отвечает", eg.get("err") or NEED_MORE, "confirmed"))
    if load is not None and load >= max_load:
        out.append(Finding("egress_load", 2 if load >= 95 else 1, "egress", "egress", f"Нагрузка узла Egress {load:.0f} %", f"Предел запуска эфиров — {max_load} %.", "confirmed"))
    if (eg.get("queue") or 0) > 2:
        out.append(Finding("egress_queue", 1, "egress", "egress", f"Egress не успевает записать {eg['queue']:.0f} сегм.", "Очередь записи файлов растёт: проверьте загрузку диска.", "assumed"))
    if (eg.get("upload_fail") or 0) > 0:
        out.append(Finding("egress_upload", 2, "egress", "output", f"Egress не смог записать файлы: {eg['upload_fail']:.0f}", "Счётчик ошибок записи Egress.", "confirmed"))
    if s.get("egress_status") and s["egress_status"] not in ("EGRESS_ACTIVE", "EGRESS_STARTING", "EGRESS_ENDING"):
        out.append(Finding("egress_state", 2, "egress", "egress", f"Состояние Egress: {s['egress_status']}", s.get("egress_error") or NEED_MORE, "confirmed"))

    if s.get("rtmp") and not s.get("relay") and any(d.get("enabled") for d in s["rtmp"]):
        out.append(Finding("relay_direct", 1, "egress", "output", "Внешние площадки подключены прямо к Egress",
                           "Служба ретрансляции live-relay не работает: одна площадка, рвущая соединение, может остановить весь эфир. Запустите её: sudo scripts/ctl.sh start live-relay.", "confirmed"))
    age, dur = hls.get("age_s"), hls.get("dur_s") or 1.5
    if s.get("status") == "live" and hls and not hls.get("ended"):
        logs = " Журнал службы: sudo scripts/logs.sh livekit-egress --tail 200."

        def why() -> tuple[str, str]:
            if eg.get("st") == "down":
                return "Egress не отвечает." + logs, "confirmed"
            if s.get("egress_status") and s["egress_status"] not in ("EGRESS_ACTIVE", "EGRESS_STARTING"):
                return f"Egress сообщил: {s.get('egress_error') or s['egress_status']}." + logs, "confirmed"
            if disk.get("free") is not None and disk["free"] < 2 * 1024**3:
                return f"На томе трансляций свободно {d1(disk['free'] / 1024**3)} ГБ: Egress держит там файл записи.", "confirmed"
            if s.get("egress_seg_grow") is True:
                return "Egress делает сегменты, но в каталог эфира они не попадают: проверьте место и права на томе трансляций (DATA_ROOT/live)." + logs, "confirmed"
            if s.get("egress_seg_grow") is False and (load is None or load < 50):
                return "Egress запущен, но не выдаёт видео: кодировщик стоит, а не перегружен. Если выступающий показывает экран или камеру, перезапустите эфир." + logs, "assumed"
            if (disk.get("util") or 0) >= 90:
                return f"Диск занят на {disk['util']:.0f} %.", "assumed"
            if (eg.get("throttled") or 0) >= 0.1:
                return "Контейнер Egress упирается в заданный предел процессора.", "confirmed"
            if load is not None and load >= 80:
                return f"Загрузка Egress составляет {load:.0f} %.", "assumed"
            return NEED_MORE, "unknown"
        if age is not None and age > max(6.0, dur * 4):
            c, k = why()
            out.append(Finding("hls_stall", 2, "hls", "output", f"Новых сегментов HLS нет {age:.0f} с", c, k))
        elif (hls.get("interval_s") or 0) > dur * 1.5 or (hls.get("ready_s") or 0) > 6:
            c, k = why()
            out.append(Finding("hls_slow", 1, "hls", "output", f"Задержка увеличивается на этапе формирования HLS-сегментов: сегмент готов через {d1(hls.get('ready_s') or 0)} с", c, k))
    if media.get("fps") is not None and media["fps"] < 24:
        out.append(Finding("fps_low", 1, "egress", "egress", f"На выходе Egress {media['fps']:.0f} кадров/с", f"Загрузка Egress {load:.0f} %." if load is not None else NEED_MORE, "assumed" if load is not None and load >= 80 else "unknown"))
    if (media.get("gaps") or 0) > 0:
        out.append(Finding("frames_gap", 1, "egress", "egress", f"Пропуски кадров в сегменте: {media['gaps']}", f"Загрузка Egress {load:.0f} %." if load is not None else NEED_MORE, "assumed" if load is not None and load >= 80 else "unknown"))

    for d in s.get("rtmp") or []:
        if d.get("status") == "failed":
            out.append(Finding(f"rtmp_{d.get('id', '')[:8]}", 2, "rtmp", "output", f"Площадка «{d.get('name')}» не принимает поток", d.get("error") or "Egress сообщил об ошибке.", "confirmed"))

    src = s.get("source") or {}
    if src.get("known") and s.get("status") == "live" and not src.get("video"):
        out.append(Finding("no_video", 1, "source", "source", "Нет ни одной видеодорожки: зрители видят заставку", "Никто не показывает экран и не включил камеру.", "confirmed"))
    want = LIVE_QUALITY.get(s.get("quality") or "", (0, 0, 0))
    if src.get("max_h") and want[1] and src["max_h"] < want[1] * 0.7:
        out.append(Finding("source_low", 1, "source", "source", f"Источник {src['max_w']}×{src['max_h']} ниже качества эфира {s.get('quality')}", "Egress растягивает изображение: чёткость определяет источник.", "confirmed"))
    for p in view.get("publishers") or []:
        if p.get("limit") in ("cpu", "bandwidth"):
            why_ = "процессор компьютера" if p["limit"] == "cpu" else "исходящий канал"
            out.append(Finding(f"pub_limit_{p['limit']}", 1, "source", "source", f"Браузер выступающего снижает качество: {why_}", "Причину сообщает сам браузер (getStats).", "confirmed"))
            break

    rep, bad = view.get("reporting") or 0, view.get("bad") or 0
    # зрители узнают о сбое позже сервера: недавняя проблема потока ещё объясняет их остановки
    server_ok = not s.get("recent_issue") and not any(hurts_viewers(f.stage, f.component, f.level) for f in out)
    if rep >= 3 and bad / rep >= 0.3:
        c, k = ("Сервер и формирование потока в норме: узкое место — доставка (канал сервера, CDN или общий участок сети).", "assumed") if server_ok else ("Ухудшение совпадает с проблемой на сервере: см. другие предупреждения.", "assumed")
        out.append(Finding("viewers_common", 2 if bad / rep >= 0.6 else 1, "delivery", "delivery", f"Проблема у {bad} из {rep} зрителей", c, k))
    elif bad:
        c, k = ("Сервер работает нормально, остальные зрители без проблем: это канал или устройство зрителя.", "assumed") if server_ok and rep >= 3 else ("Отчётов зрителей мало, чтобы разделить сервер и зрителя.", "unknown") if rep < 3 else ("Возможна связь с проблемой на сервере.", "assumed")
        w = (view.get("worst") or [{}])[0]
        out.append(Finding("viewer_single", 1, "viewer", "delivery", f"Проблема у отдельных зрителей ({bad}): {w.get('why') or 'качество ниже нормы'}", c, k))
    return out


STAGES = (("source", "Источник"), ("livekit", "LiveKit"), ("egress", "Egress"), ("output", "HLS/RTMP"), ("delivery", "Зрители"))


def stage_view(s: dict, findings: list[Finding]) -> list[dict]:
    """Пять этапов цепочки: уровень (−1 нет данных, 0 норма, 1 предупреждение, 2 критично) и главная величина."""
    svc, hls, view, media, src = s.get("svc") or {}, s.get("hls") or {}, s.get("viewers") or {}, s.get("media") or {}, s.get("source") or {}
    lk, eg = svc.get("livekit") or {}, svc.get("egress") or {}
    pub = (view.get("publishers") or [{}])[0]
    val = {
        "source": f"{src['max_w']}×{src['max_h']}" if src.get("max_h") else ("нет видео" if src.get("known") else None),
        "livekit": f"{d1(max(lk.get('loss_in') or 0, lk.get('loss_out') or 0))} %" if lk.get("loss_in") is not None or lk.get("loss_out") is not None else (lk.get("st") and "работает"),
        "egress": f"{eg['load']:.0f} %" if eg.get("load") is not None else eg.get("st"),
        "output": f"{d1(hls['ready_s'])} с" if hls.get("ready_s") is not None else (f"{len(s.get('rtmp') or [])} площ." if s.get("rtmp") else None),
        "delivery": f"{d1(view['lat'])} с" if view.get("lat") is not None else (f"{s.get('viewer_count') or 0} зрит."),
    }
    known = {"source": bool(src.get("known") or pub), "livekit": bool(lk), "egress": bool(eg), "output": bool(hls or s.get("rtmp")), "delivery": bool(view.get("reporting"))}
    out = []
    for sid, name in STAGES:
        lv = max((f.level for f in findings if f.stage == sid), default=0)
        out.append({"id": sid, "name": name, "lv": lv if lv or known[sid] else -1, "val": val[sid]})
    return out


def snapshot_of(s: dict) -> dict:
    """Показатели в момент события: по ним причину можно установить и после того, как проблема исчезла."""
    h, sv, hl, v, md = s.get("host") or {}, s.get("svc") or {}, s.get("hls") or {}, s.get("viewers") or {}, s.get("media") or {}
    eg = sv.get("egress") or {}
    lk = sv.get("livekit") or {}
    return {"cpu": h.get("cpu"), "mem_avail_pct": (h.get("mem") or {}).get("avail_pct"), "disk_free_gb": round((h.get("disk") or {}).get("free", 0) / 1024**3, 1) if (h.get("disk") or {}).get("free") is not None else None,
            "disk_util": (h.get("disk") or {}).get("util"), "tx_mbit": round((h.get("net") or {}).get("tx_bps", 0) / 1e6, 1) if (h.get("net") or {}).get("tx_bps") is not None else None,
            "egress_load": eg.get("load"), "egress_cores": eg.get("cores"), "lk_loss_in": lk.get("loss_in"), "lk_loss_out": lk.get("loss_out"), "seg_age_s": hl.get("age_s"), "seg_ready_s": hl.get("ready_s"),
            "fps": md.get("fps"), "video_kbps": md.get("vkbps"), "viewers": s.get("viewer_count"), "viewers_bad": v.get("bad"), "latency_s": v.get("lat")}


RING_KEYS = ("t", "cpu", "mem", "rx", "tx", "load", "cores", "age", "ready", "lat", "viewers", "stalls", "vkbps", "fps", "dw", "util")


def ring_point(s: dict) -> list:
    h, sv, hl, v, md = s.get("host") or {}, s.get("svc") or {}, s.get("hls") or {}, s.get("viewers") or {}, s.get("media") or {}
    n, d, eg = h.get("net") or {}, h.get("disk") or {}, sv.get("egress") or {}
    return [round(s.get("t") or time.time(), 1), h.get("cpu"), (h.get("mem") or {}).get("used_pct"), n.get("rx_bps"), n.get("tx_bps"), eg.get("load"), eg.get("cores"), hl.get("age_s"), hl.get("ready_s"), v.get("lat"),
            s.get("viewer_count"), v.get("stalls"), md.get("vkbps") or hl.get("kbps"), md.get("fps"), d.get("wr_bps"), d.get("util")]


def summarize(samples: list[dict], events: list[Any], b: Broadcast, *, version: str = "", mode: dict | None = None) -> dict:
    """Итог эфира для истории и сравнения: нагрузка, качество, задержка, зрители, события."""
    def col(*path: str) -> list[float]:
        vals = []
        for s in samples:
            v: Any = s
            for p in path:
                v = v.get(p) if isinstance(v, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        return vals

    def stat(*path: str) -> dict | None:
        xs = col(*path)
        return {"avg": round(sum(xs) / len(xs), 2), "max": round(max(xs), 2)} if xs else None

    ended = b.ended_at or datetime.now(timezone.utc)
    levels: dict[str, int] = {}
    for e in events:
        levels[e.level] = levels.get(e.level, 0) + 1
    res = [s.get("media", {}).get("res") for s in samples if isinstance(s.get("media"), dict) and s["media"].get("res")]
    return {"schema": 1, "version": version, "quality": b.quality, "status": b.status, "error": clean_text(b.error) if b.error else None, "started_at": b.started_at.isoformat(), "ended_at": b.ended_at.isoformat() if b.ended_at else None,
            "duration_s": round((ended - b.started_at).total_seconds()), "samples": len(samples), "mode": mode or {"hls": bool(b.hls_dir)}, "res": res[-1] if res else None,
            "viewers": stat("viewer_count"), "peak_viewers": b.peak_viewers, "cpu": stat("host", "cpu"), "mem_used_pct": stat("host", "mem", "used_pct"), "tx_mbit": {k: round(v / 1e6, 1) for k, v in (stat("host", "net", "tx_bps") or {}).items()} or None,
            "disk_util": stat("host", "disk", "util"), "egress_load": stat("svc", "egress", "load"), "egress_cores": stat("svc", "egress", "cores"), "seg_ready_s": stat("hls", "ready_s"), "seg_interval_s": stat("hls", "interval_s"),
            "video_kbps": stat("media", "vkbps"), "audio_kbps": stat("media", "akbps"), "fps": stat("media", "fps"), "latency_s": stat("viewers", "lat"), "seg_ms": stat("viewers", "seg_ms"),
            "stalls": int((col("viewers", "stalls_total") or [0])[-1]), "reconnects": int((col("viewers", "reconn_total") or [0])[-1]), "events": levels}


# ------------------------------------------------------------------------------------------------ сборщик
class LiveDiag:
    def __init__(self, settings, session_maker, redis, settings_svc, *, live, perf, journal=None, cdn=None):
        self._s, self._sm, self._r, self._svc = settings, session_maker, redis, settings_svc
        self.live, self.perf, self.journal, self.cdn = live, perf, journal, cdn
        self.rates = RateTracker()
        self._cores: dict[str, tuple[int, int]] = {}
        self._disk: tuple[float, dict] | None = None
        self._net: tuple[float, list[int]] | None = None
        self._egress_cpu: tuple[float, int, int] | None = None
        self._cores_view: dict = {}
        self._host: dict = {}
        self._svc_view: dict = {}
        self._starts: dict[str, float] = {}
        self._b: dict[uuid.UUID, dict] = {}        # состояние сбора по идущим эфирам
        self._retained = 0.0
        self._lag_ms: float | None = None

    # ---------------------------------------------------------------- хост
    def sample_host(self, now: float, live_dir: Path, cfg: dict) -> dict:
        cores = read_cores()
        pct: dict[str, float] = {}
        for k, (busy, total) in cores.items():
            p = self._cores.get(k)
            if p and total > p[1]:
                pct[k] = round(100.0 * (busy - p[0]) / (total - p[1]), 1)
        self._cores = cores
        per = [pct[k] for k in sorted((c for c in pct if c != "cpu"), key=lambda x: int(x[3:]))]
        mem = read_meminfo() or {}
        total, avail = mem.get("MemTotal"), mem.get("MemAvailable")
        out: dict[str, Any] = {"cpu": pct.get("cpu"), "cores": per, "cpus": os.cpu_count() or 1, "load": read_loadavg() or None,
                               "mem": {"total": total, "avail": avail, "free": mem.get("MemFree"), "used_pct": round(100 - avail * 100 / total, 1) if total and avail is not None else None,
                                       "avail_pct": round(avail * 100 / total, 1) if total and avail is not None else None}}
        disk: dict[str, Any] = {}
        try:
            u = shutil.disk_usage(live_dir if live_dir.exists() else live_dir.parent)
            disk = {"free": u.free, "total": u.total, "used_pct": round(u.used * 100 / u.total, 1) if u.total else None}
        except OSError:
            pass
        cur = read_disk(device_of(live_dir))
        if cur is not None:
            if self._disk is not None and now > self._disk[0]:
                dt, p = now - self._disk[0], self._disk[1]
                ops = cur["r"] + cur["w"] - p["r"] - p["w"]
                disk.update(rd_bps=round((cur["rs"] - p["rs"]) * 512 / dt), wr_bps=round((cur["ws"] - p["ws"]) * 512 / dt), util=round(min(100.0, (cur["busy"] - p["busy"]) / dt / 10), 1),
                            await_ms=round((cur["rms"] + cur["wms"] - p["rms"] - p["wms"]) / ops, 1) if ops > 0 else 0.0)
            self._disk = (now, cur)
        out["disk"] = disk
        scope, nets = read_netdev()
        net: dict[str, Any] = {"scope": scope}
        if nets:
            name = max(nets, key=lambda k: nets[k][0] + nets[k][3])
            vals = [sum(v[i] for v in nets.values()) for i in range(6)]
            net["if"] = name
            if self._net is not None and now > self._net[0]:
                dt, p = now - self._net[0], self._net[1]
                d = [max(0, a - b) for a, b in zip(vals, p)]
                net.update(rx_bps=round(d[0] * 8 / dt), tx_bps=round(d[3] * 8 / dt), err_s=round((d[1] + d[4]) / dt, 2), drop_s=round((d[2] + d[5]) / dt, 2))
                peak = self._host.get("net", {}).get("peak_bps", 0)
                net["peak_bps"] = max(peak, net["tx_bps"], net["rx_bps"])
            self._net = (now, vals)
        mbit = cfg.get("diag_net_mbps") or 0
        net["cap_bps"] = mbit * 1_000_000 or None
        out["net"] = net
        return out

    # ---------------------------------------------------------------- службы
    async def _get(self, c: httpx.AsyncClient, url: str) -> tuple[str | None, float, str | None]:
        t0 = time.perf_counter()
        try:
            r = await c.get(url)
        except httpx.HTTPError as exc:
            return None, 0.0, type(exc).__name__
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return (r.text if r.status_code == 200 else None), ms, (None if r.status_code == 200 else f"HTTP {r.status_code}")

    def _restart_check(self, name: str, start: float | None, fresh: list[str]) -> None:
        if start:
            old = self._starts.get(name)
            if old and start > old + 5:                       # время запуска Redis считается от часов и его uptime: допуск на округление
                fresh.append(name)
            self._starts[name] = start

    async def sample_services(self, cfg: dict, live_dir: Path) -> tuple[dict, list[str]]:
        """Состояние служб. Сокета Docker у приложения нет и не будет: показатели идут из собственных портов метрик служб, недоступное — None."""
        out: dict[str, dict] = {}
        restarted: list[str] = []
        now = time.time()
        h = self.perf.host.sample()
        out["backend"] = {"st": "ok", "cpu": h.container_cpu_pct, "mem": h.container_mem, "lag_ms": self._lag_ms}

        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(self._r.ping(), 2)
            info = await asyncio.wait_for(self._r.info(), 2)
            out["redis"] = {"st": "ok", "ms": round((time.perf_counter() - t0) * 500, 1), "mem": info.get("used_memory"), "clients": info.get("connected_clients"), "ops": info.get("instantaneous_ops_per_sec")}
            self._restart_check("redis", now - float(info.get("uptime_in_seconds") or 0), restarted)
        except Exception as exc:  # noqa: BLE001 — Redis недоступен: это и есть показатель
            out["redis"] = {"st": "down", "err": type(exc).__name__}
        t0 = time.perf_counter()
        try:
            from sqlalchemy import text  # noqa: PLC0415

            async with self._sm() as db:
                await asyncio.wait_for(db.execute(text("select 1")), 2)
            out["postgres"] = {"st": "ok", "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            out["postgres"] = {"st": "down", "err": type(exc).__name__}

        egress_url = str(cfg.get("egress_health_url") or "")
        eg_host = urlsplit(egress_url).hostname or "livekit-egress"
        web_host = urlsplit(getattr(self._s, "recorder_base_url", "") or "http://web:8080").hostname or "web"
        lk_u = urlsplit(self._s.livekit_http_url)
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5), follow_redirects=False) as c:
            lk_root, ng, eg_m, lk_m = await asyncio.gather(
                self._get(c, f"{lk_u.scheme}://{lk_u.netloc}/"), self._get(c, f"http://{web_host}:8081/stub"), self._get(c, f"http://{eg_host}:9090/metrics"), self._get(c, self._lk_metrics_url()))
        out["nginx"] = self._nginx(ng, now)
        out["livekit"] = self._livekit(lk_root, lk_m, restarted)
        out["egress"] = self._egress(eg_m, live_dir, restarted, cfg)
        try:
            raw = await asyncio.wait_for(self._r.get("asr:heartbeat"), 1)
            hb = json.loads(raw) if raw else None
            out["asr"] = {"st": "ok" if hb else "unknown", "queue": (hb or {}).get("queue_depth")}
        except Exception:  # noqa: BLE001
            out["asr"] = {"st": "unknown"}
        return out, restarted

    def _lk_metrics_url(self) -> str:
        from .perf import metrics_url  # noqa: PLC0415

        return metrics_url(self._s)

    def _nginx(self, r: tuple[str | None, float, str | None], now: float) -> dict:
        text_, ms, err = r
        if text_ is None:
            return {"st": "down" if err and "HTTP" not in err else "unknown", "err": err}
        m = re.search(r"Active connections:\s*(\d+)\s+server accepts handled requests\s+(\d+)\s+(\d+)\s+(\d+)\s+Reading:\s*(\d+)\s+Writing:\s*(\d+)\s+Waiting:\s*(\d+)", text_)
        if not m:
            return {"st": "ok", "ms": ms}
        rps = self.rates.rate("ng_req", float(m.group(4)), now)
        return {"st": "ok", "ms": ms, "conns": int(m.group(1)), "writing": int(m.group(6)), "rps": None if rps is None else round(rps, 1)}

    def _livekit(self, root: tuple[str | None, float, str | None], metrics: tuple[str | None, float, str | None], restarted: list[str]) -> dict:
        text_, ms, err = root
        out: dict[str, Any] = {"st": "ok" if text_ is not None else "down", "ms": ms if text_ is not None else None, "err": err}
        if metrics[0] is None:
            return out
        m = parse_prometheus(metrics[0], {"livekit_packet_bytes": "bytes", "livekit_packet_total": "pkts", "livekit_packet_loss_total": "loss", "livekit_nack_total": "nack", "livekit_pli_total": "pli",
                                          "livekit_node_packet_total": "nodepk", "process_cpu_seconds_total": "cpu", "process_resident_memory_bytes": "rss", "process_start_time_seconds": "start"})
        t = time.time()

        def total(key: str, **labels: str) -> float | None:
            xs = [v for lab, v in m.get(key, []) if all(lab.get(k) == x for k, x in labels.items())]
            return sum(xs) if xs else None

        def rate(key: str, value: float | None) -> float | None:
            return self.rates.rate(f"lk_{key}", value, t)

        for d, name in (("incoming", "in_kbps"), ("outgoing", "out_kbps")):
            r = rate(f"b_{d}", total("bytes", direction=d))
            out[name] = None if r is None else round(r * 8 / 1000, 1)
        for d, name in (("incoming", "loss_in"), ("outgoing", "loss_out")):
            lost, sent = rate(f"l_{d}", total("loss", direction=d)), rate(f"p_{d}", total("pkts", direction=d))
            if lost is not None and sent:
                out[name] = round(min(100.0, lost * 100 / (sent + lost)), 2)
        nack, pli, dropped = rate("nack", total("nack")), rate("pli", total("pli")), rate("drop", total("nodepk", type="dropped"))
        out.update(nack_s=None if nack is None else round(nack, 1), pli_s=None if pli is None else round(pli, 2), drop_s=None if dropped is None else round(dropped, 1))
        cpu = rate("cpu", total("cpu"))
        out.update(cores=None if cpu is None else round(cpu, 2), mem=total("rss"))
        self._restart_check("livekit", total("start"), restarted)
        return out

    def _egress(self, metrics: tuple[str | None, float, str | None], live_dir: Path, restarted: list[str], cfg: dict) -> dict:
        text_, ms, err = metrics
        out: dict[str, Any] = {"st": "ok" if text_ is not None else ("unknown" if err and err.startswith("HTTP") else "down"), "ms": ms if text_ is not None else None, "err": err}
        raw = read_cgroup_cpu(live_dir / EGRESS_CPU_FILE)
        if raw is not None:
            usec, at, cut = raw
            p = self._egress_cpu
            if p is not None and at > p[0] and usec >= p[1]:
                self._cores_view = {"cores": round((usec - p[1]) / 1e6 / (at - p[0]), 2), "throttled": round((cut - p[2]) / 1e6 / (at - p[0]), 3)}
            out.update(self._cores_view)                                     # файл обновляется раз в 15 с: между обновлениями — прежнее значение
            if p is None or at > p[0]:
                self._egress_cpu = (at, usec, cut)
        if text_ is None:
            return out
        m = parse_prometheus(text_, {"livekit_node_cpu_load": "cpu", "livekit_egress_cgroup_memory_bytes": "mem", "livekit_egress_proc_rss_bytes": "rss", "livekit_egress_requests": "req", "livekit_egress_can_accept_request": "accept",
                                     "livekit_egress_is_terminating": "term", "livekit_egress_segments_uploads_channel_size": "segq", "livekit_egress_playlist_uploads_channel_size": "plq", "livekit_egress_pipeline_uploads": "up",
                                     "livekit_egress_pipline_upload_response_time_ms_sum": "upsum", "livekit_egress_pipline_upload_response_time_ms_count": "upcnt", "process_start_time_seconds": "start"})
        val = lambda k: sum(v for _, v in m.get(k, [])) if m.get(k) else None          # noqa: E731
        t = time.time()
        load = val("cpu")
        out.update(load=None if load is None else round(load * 100, 1), mem=val("mem") or val("rss"), jobs=val("req"), accept=val("accept") == 1 if m.get("accept") else None, term=bool(val("term")), queue=(val("segq") or 0) + (val("plq") or 0))
        fail = sum(v for lab, v in m.get("up", []) if lab.get("status") != "success")
        rate = self.rates.rate("eg_fail", fail, t)
        out["upload_fail"] = round(rate * 6) if rate else 0
        su, sc = self.rates.rate("eg_upsum", val("upsum"), t), self.rates.rate("eg_upcnt", val("upcnt"), t)
        if su is not None and sc:
            out["upload_ms"] = round(su / sc, 1)
        self._restart_check("egress", val("start"), restarted)
        return out

    # ---------------------------------------------------------------- цепочка эфира
    async def _participants(self, room_name: str) -> list[dict] | None:
        from livekit import api as lkapi  # noqa: PLC0415

        try:
            async with lkapi.LiveKitAPI(self._s.livekit_http_url, self._s.livekit_api_key, self._s.livekit_api_secret) as lk:
                r = await asyncio.wait_for(lk.room.list_participants(lkapi.ListParticipantsRequest(room=room_name)), 3)
        except Exception:  # noqa: BLE001 — сервер звонков недоступен: это покажет его состояние
            return None
        out = []
        for p in r.participants:
            if p.identity.startswith(("EG_", "asr-", "rec-")):
                continue
            tracks = []
            for t in p.tracks:
                tracks.append({"kind": "video" if t.type == 1 else "audio", "src": {1: "camera", 2: "mic", 3: "screen", 4: "screen_audio"}.get(int(t.source), "other"), "w": t.width or None, "h": t.height or None, "muted": bool(t.muted), "sim": bool(t.simulcast)})
            out.append({"id": p.identity[:48], "name": (p.name or p.identity)[:80], "tracks": tracks})
        return out

    def _destinations(self, rows: list[LiveDestination]) -> list[dict]:
        return [{"id": str(r.id), "name": r.name, "platform": PLATFORMS.get(r.platform, r.platform), "status": r.status, "enabled": r.enabled, "error": clean_text(r.last_error, 200) if r.last_error else None} for r in rows if r.enabled]

    def _rtmp_stats(self, info, rows: list[LiveDestination], dests: list[dict]) -> None:
        """Состояние каждой площадки по ответу Egress: поток, время передачи, повторные подключения. Скорость Egress не сообщает: у всех площадок один и тот же поток кодировщика."""
        results = list(getattr(info, "stream_results", None) or [])
        api = self.live.destinations
        if not results or api is None:
            return
        from livekit.protocol import egress as eg  # noqa: PLC0415

        by_id = {str(r.id): r for r in rows}
        for d in dests:
            row = by_id[d["id"]]
            key = api._decrypt(row)                  # noqa: SLF001 — ключ нужен только для сопоставления с адресом, который Egress возвращает со скрытым ключом
            sr = next((x for x in results if api._match(x.url, row, key)), None)        # noqa: SLF001
            if sr is not None:
                d.update(egress=eg.StreamInfo.Status.Name(sr.status), retries=int(sr.retries) if hasattr(sr, "retries") else None, duration_s=round(sr.duration / 1e9) if sr.duration else None)

    async def _clients(self, bid: uuid.UUID, st: dict, seg_s: float, need_kbps: float, now: float) -> dict:
        try:
            raw = await self._r.hgetall(K_CLIENTS.format(bid))
        except Exception:  # noqa: BLE001
            return {}
        rows: dict[str, dict] = {}
        stale = []
        for k, v in raw.items():
            k = k.decode() if isinstance(k, bytes) else k
            try:
                d = json.loads(v)
            except ValueError:
                continue
            if now - float(d.get("t", 0)) > CLIENT_MAX_AGE_S * 2:
                stale.append(k)
            else:
                rows[k] = d
        if stale:
            try:
                await self._r.hdel(K_CLIENTS.format(bid), *stale[:1000])
            except Exception:  # noqa: BLE001
                pass
        out = aggregate_clients(rows, st.get("clients") or {}, seg_s, need_kbps, now, st.setdefault("verdicts", {}))
        st["clients"] = rows
        return out

    async def report_client(self, bid: uuid.UUID, key: str, stats: Any, ua: str | None) -> None:
        """Отчёт браузера (из пульса зрителя или от участника комнаты). Никогда не мешает основному запросу."""
        c = clean_client(stats)
        if c is None:
            return
        c.update(t=round(time.time(), 1), ua=(ua or "")[:60] or None)
        k = K_CLIENTS.format(bid)
        try:
            if int(await self._r.hlen(k)) >= CLIENTS_MAX and not await self._r.hexists(k, key):
                return
            p = self._r.pipeline()
            p.hset(k, key, json.dumps(c, ensure_ascii=False))
            p.expire(k, 300)
            await p.execute()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- журнал
    async def event(self, b: Broadcast | uuid.UUID, component: str, level: str, code: str, message: str, *, cause: str | None = None, kind: str | None = None, details: dict | None = None,
                    at: datetime | None = None, meeting_id: str | None = None) -> None:
        bid = b.id if isinstance(b, Broadcast) else b
        st = self._b.setdefault(bid, {})
        st["events"] = st.get("events", 0) + 1
        if (st["events"] > MAX_EVENTS and level == "info") or st["events"] > MAX_EVENTS * 2:
            return
        msg = clean_text(message)
        try:
            async with self._sm() as db:
                db.add(LiveDiagEvent(broadcast_id=bid, at=at or datetime.now(timezone.utc), component=component[:16], level=level, code=code[:48], message=msg, cause=clean_text(cause) if cause else None, cause_kind=kind,
                                     details=clean(details) if details else None))
                await db.commit()
        except Exception:  # noqa: BLE001 — журнал не должен мешать эфиру
            log.warning("Событие журнала эфира не записано", extra={"code": code})
        if self.journal is not None and level != "info":
            self.journal.emit("live", f"diag_{code}"[:64], level=level, meeting_id=meeting_id or st.get("meeting_id"), room=st.get("room"), message=msg, data={"broadcast": str(bid), "component": component, "cause": clean_text(cause, 200) if cause else None})

    def _diff_participants(self, st: dict, parts: list[dict]) -> list[tuple[str, str]]:
        cur = {p["id"]: p for p in parts}
        old = st.get("parts")
        st["parts"] = cur
        if old is None:
            return []
        out = [("participant_joined", f"Подключился участник: {cur[k]['name']}") for k in cur.keys() - old.keys()]
        out += [("participant_left", f"Отключился участник: {old[k]['name']}") for k in old.keys() - cur.keys()]
        for k in cur.keys() & old.keys():
            def vid(p: dict) -> dict:
                return {(t["src"]): (t["w"], t["h"]) for t in p["tracks"] if t["kind"] == "video" and not t["muted"]}
            a, b_ = vid(old[k]), vid(cur[k])
            if a != b_:
                out.append(("track_changed", f"{cur[k]['name']}: видео {', '.join(f'{s} {w}×{h}' for s, (w, h) in b_.items()) or 'выключено'}"))
        return out

    async def _begin(self, b: Broadcast, room: Room | None, meeting: Meeting | None) -> dict:
        async with self._sm() as db:           # этапы запуска пишутся до сборщика, поэтому «возобновление» узнаётся по его собственной первой записи
            n = int((await db.execute(select(func.count()).select_from(LiveDiagEvent).where(LiveDiagEvent.broadcast_id == b.id, LiveDiagEvent.code == "started"))).scalar_one())
        st = self._b.setdefault(b.id, {})
        st.update(events=n, room=room.name if room else None, meeting_id=str(b.meeting_id), room_id=b.room_id, status=b.status, lk=meeting.livekit_room if meeting else None, findings={}, last_persist=0.0, last_probe=0.0, media={}, cdn=bool(room and room.live_cdn))
        if n == 0:
            await self.event(b, "broadcast", "info", "started", f"Эфир начат: качество {b.quality}" + (f", запустил {b.started_by}" if b.started_by else ""), at=b.started_at)
        else:
            await self.event(b, "server", "warn", "collector_resumed", "Сбор показателей возобновлён: приложение перезапускалось во время эфира", kind="confirmed")
        return st

    async def _track_findings(self, b: Broadcast, st: dict, s: dict, found: list[Finding]) -> list[dict]:
        """Находка становится событием, если держится два опроса подряд (12 с), и закрывается после трёх чистых: кратковременные всплески журнал не засоряют."""
        act: dict[str, dict] = st["findings"]
        seen = set()
        for f in found:
            seen.add(f.code)
            a = act.setdefault(f.code, {"hits": 0, "clear": 0, "open": False, "level": 0, "since": s["t"]})
            a.update(hits=a["hits"] + 1, clear=0, f=asdict(f))
            if a["hits"] >= 2 and (not a["open"] or f.level > a["level"]):
                a.update(open=True, level=f.level)
                await self.event(b, f.component, LEVEL_NAME[f.level], f.code, f.text, cause=f.cause, kind=f.kind, details={"snapshot": snapshot_of(s)})
        for code in list(act):
            if code in seen:
                continue
            a = act[code]
            a["clear"] += 1
            # отчёты зрителей приходят раз в 30 с: их находки не должны мигать между отчётами
            if a["clear"] >= (CLEAR_VIEWER if code.startswith("viewer") else CLEAR_POLLS) or not a["open"]:
                if a["open"]:
                    fd = a["f"]
                    await self.event(b, fd["component"], "info", f"{code}_end"[:48], f"Прошло: {fd['text']}", details={"lasted_s": round(s["t"] - a["since"]), "snapshot": snapshot_of(s)})
                del act[code]
        return [{**a["f"], "since": a["since"]} for a in act.values() if a["open"]]

    async def _slow(self, b: Broadcast, room: Room | None, meeting: Meeting | None, st: dict, cfg: dict, base: dict, services: dict, restarted: list[str], now: float) -> dict:
        s: dict[str, Any] = {**base, "svc": services, "status": b.status, "quality": b.quality, "media": st.get("media") or {}}
        ldir = self.live.live_dir()
        s["hls"] = None
        hdir = ldir / "b" / b.hls_dir if b.hls_dir else None
        if hdir is not None:
            s["hls"] = await asyncio.to_thread(hls_stats, hdir)
            h = s["hls"]
            if h and room is not None and room.live_view_access and cfg.get("ll_hls", True):
                h["ll"] = await self.live.ll_ready(room.live_view_access)     # зрители с низкой задержкой: если служба встала, они уже ушли на обычный HLS, и это надо видеть
            if h and h.get("name") and now - st.get("last_probe", 0) >= PROBE_S:
                st["last_probe"] = now
                probed = await probe_segment(hdir / h["name"])
                if probed:
                    await self._media_change(b, st, probed)
                    st["media"] = probed
                    s["media"] = probed
            if h and h.get("disc", 0) > 0 and st.get("disc_seq") != h.get("seq"):
                st["disc_seq"] = h.get("seq")
                await self.event(b, "hls", "warn", "hls_discontinuity", "Разрыв в потоке HLS: плееры начнут сегмент заново", details={"snapshot": snapshot_of(s)})
        info = None
        if b.egress_id:
            try:
                info = await asyncio.wait_for(self.live.api.get(b.egress_id), 3)
            except Exception:  # noqa: BLE001
                info = None
        if info is not None:
            from livekit.protocol import egress as eg  # noqa: PLC0415

            s["egress_status"] = eg.EgressStatus.Name(info.status)
            seg = info.segment_results[0].segment_count if info.segment_results else None
            if seg is not None:                         # счётчик самого Egress: растёт — кодирует; стоит — кадров нет
                prev_seg, st["eg_seg"] = st.get("eg_seg"), seg
                s["egress_seg_grow"] = None if prev_seg is None else seg > prev_seg
            s["egress_error"] = clean_text(info.error, 200) if info.error else None
        async with self._sm() as db:
            rows = list((await db.execute(select(LiveDestination).where(LiveDestination.room_id == b.room_id).order_by(LiveDestination.position))).scalars())
            nodes = [n for n in await self.cdn.nodes(db)] if st.get("cdn") and self.cdn is not None else []
        s["rtmp"] = self._destinations(rows)
        try:
            s["relay"] = bool(await self._r.get(f"live:relay:{b.id}"))
        except Exception:  # noqa: BLE001
            s["relay"] = False
        if info is not None:
            self._rtmp_stats(info, rows, s["rtmp"])
        prev_d = st.get("dest") or {}
        for d in s["rtmp"]:
            was = prev_d.get(d["id"])
            if was is not None and was != d["status"]:
                st.setdefault("dest_flips", {}).setdefault(d["id"], 0)
                st["dest_flips"][d["id"]] += 1
                lv = "error" if d["status"] == "failed" else "info"
                await self.event(b, "rtmp", lv, "rtmp_state", f"Площадка «{d['name']}»: {was} → {d['status']}", cause=d.get("error"), kind="confirmed" if d.get("error") else None, details={"snapshot": snapshot_of(s)})
            d["flips"] = (st.get("dest_flips") or {}).get(d["id"], 0)
        st["dest"] = {d["id"]: d["status"] for d in s["rtmp"]}
        if nodes:
            s["cdn"] = [{"name": n.name, "state": n.state, "viewers": (n.stats or {}).get("viewers"), "mbit": (n.stats or {}).get("out_mbit"), "hit": (n.stats or {}).get("hit_ratio_now"), "cpu": (n.stats or {}).get("cpu_pct")} for n in nodes]

        parts = await self._participants(st["lk"]) if st.get("lk") else None
        if parts is not None:
            for code, msg in self._diff_participants(st, parts):
                await self.event(b, "source", "info", code, msg)
            vids = [t for p in parts for t in p["tracks"] if t["kind"] == "video" and not t["muted"]]
            big = max(vids, key=lambda t: (t.get("h") or 0), default=None)
            s["source"] = {"known": True, "participants": len(parts), "audio": sum(1 for p in parts for t in p["tracks"] if t["kind"] == "audio" and not t["muted"]), "video": len(vids),
                           "max_w": big and big.get("w"), "max_h": big and big.get("h"), "list": parts[:12]}
        count = await self.live.viewers(b.room_id)
        s["viewer_count"] = count
        if count > st.get("peak", 0):
            st["peak"] = count
            if count > b.peak_viewers:
                async with self._sm() as db:
                    await db.execute(update(Broadcast).where(Broadcast.id == b.id, Broadcast.peak_viewers < count).values(peak_viewers=count))
                    await db.commit()
        was_n = st.get("viewer_n")
        if was_n is not None and abs(count - was_n) >= max(1, int(was_n * 0.1)):
            st["viewer_n"] = count
            await self.event(b, "viewer", "info", "viewers", f"Зрителей: {count} (было {was_n})")
        elif was_n is None:
            st["viewer_n"] = count
        need = (s["media"].get("vkbps") or 0) + (s["media"].get("akbps") or 0) or (s["hls"] or {}).get("kbps") or LIVE_QUALITY.get(b.quality, (0, 0, 3000))[2]
        s["viewers"] = await self._clients(b.id, st, (s["hls"] or {}).get("dur_s") or 1.5, need, now)
        v = s["viewers"]
        # в базу попадает каждый третий снимок: итог эфира считается здесь нарастающим, сумма сохранённых снимков занижала его втрое
        total = st.setdefault("total", {"stalls": 0, "reconn": 0})
        for k in total:
            total[k] += v.get(k) or 0
        v["stalls_total"], v["reconn_total"] = total["stalls"], total["reconn"]
        acc = st.setdefault("trouble", {"stalls": 0, "reconn": 0, "errs": 0})
        for k in acc:
            acc[k] += v.get(k) or 0
        if any(acc.values()) and now - st.get("trouble_at", 0) >= 30:          # при общей проблеме зрителей много: журнал получает итог раз в 30 с, а не событие на каждого
            st["trouble_at"] = now
            lvl = "warn" if acc["errs"] or acc["reconn"] or acc["stalls"] > 2 else "info"
            await self.event(b, "viewer", lvl, "viewer_trouble", f"Зрители: остановок {acc['stalls']}, переподключений {acc['reconn']}, ошибок {acc['errs']}", details={"worst": v.get("worst"), "snapshot": snapshot_of(s)})
            st["trouble"] = {"stalls": 0, "reconn": 0, "errs": 0}
        for name in restarted:
            await self.event(b, name, "warn", "component_restart", f"Перезапуск службы: {name}", kind="confirmed", cause="Изменилось время запуска процесса службы.", details={"snapshot": snapshot_of(s)})
        was = st.get("status")
        if was != b.status:
            st["status"] = b.status
            await self.event(b, "broadcast", "info", f"status_{b.status}", {"live": "Эфир виден зрителям", "ending": "Эфир останавливается", "starting": "Эфир запускается"}.get(b.status, f"Состояние эфира: {b.status}"))
        if any(hurts_viewers(f["stage"], f["component"], f["level"]) for f in st.get("last", {}).get("findings") or []):
            st["issue_at"] = now
        s["recent_issue"] = now - st.get("issue_at", 0) < 40          # отчёт зрителя может быть старше на 30 с
        found = rules(s, cfg)
        s["findings"] = await self._track_findings(b, st, s, found)
        s["stages"] = stage_view(s, [Finding(**{k: v for k, v in f.items() if k != "since"}) for f in s["findings"]])
        return s

    async def _media_change(self, b: Broadcast, st: dict, new: dict) -> None:
        old = st.get("media") or {}
        if old and old.get("res") != new.get("res"):
            await self.event(b, "egress", "warn", "res_changed", f"Разрешение потока изменилось: {old.get('res')} → {new.get('res')}")
        if old.get("fps") and new.get("fps") and abs(old["fps"] - new["fps"]) >= 5:
            await self.event(b, "egress", "warn", "fps_changed", f"Кадры в секунду изменились: {old['fps']:.0f} → {new['fps']:.0f}")
        if old.get("vkbps") and new.get("vkbps") and abs(old["vkbps"] - new["vkbps"]) > old["vkbps"] * 0.5:
            await self.event(b, "egress", "info", "bitrate_changed", f"Битрейт видео изменился: {old['vkbps']} → {new['vkbps']} кбит/с")

    async def _store(self, bid: uuid.UUID, s: dict, *, ring: bool) -> None:
        try:
            p = self._r.pipeline()
            p.set(K_NOW.format(bid), json.dumps(s, ensure_ascii=False, default=str), ex=TTL_NOW_S)
            if ring:
                k = K_RING.format(bid)
                p.rpush(k, json.dumps(ring_point(s)))
                p.ltrim(k, -RING_POINTS, -1)
                p.expire(k, 3600)
            await p.execute()
        except Exception:  # noqa: BLE001 — Redis недоступен: снимок пропускаем, журнал и база работают
            pass

    async def _persist(self, bid: uuid.UUID, st: dict, s: dict, free: int | None) -> None:
        if free is not None and free < MIN_FREE_BYTES:
            return
        data = {k: v for k, v in s.items() if k != "stages"}
        data["host"] = {k: v for k, v in (s.get("host") or {}).items() if k != "cores"}
        cores = (s.get("host") or {}).get("cores") or []
        if cores:
            data["host"]["core_max"] = max(cores)
        if isinstance(data.get("source"), dict):
            data["source"] = {k: v for k, v in data["source"].items() if k != "list"}
        try:
            async with self._sm() as db:
                db.add(LiveDiagSample(broadcast_id=bid, at=datetime.now(timezone.utc), data=json.loads(json.dumps(clean(data), default=str))))
                await db.commit()
        except Exception:  # noqa: BLE001
            log.warning("Показатели эфира не записаны", extra={"broadcast": str(bid)})

    async def finish(self, bid: uuid.UUID) -> None:
        """Эфир закончился: итог для истории; показатели остаются в базе, Redis освобождается."""
        async with self._sm() as db:
            b = await db.get(Broadcast, bid)
            if b is None:
                return
            if b.status not in LIVE_ACTIVE:
                msg = "Эфир завершён" if b.status == "ended" else "Эфир прерван"
                await self.event(b, "broadcast", "info" if b.status == "ended" else "error", f"finished_{b.status}", msg + (f": {clean_text(b.error, 200)}" if b.error else ""), at=b.ended_at, kind="confirmed" if b.error else None)
            await self.summary(db, b)
        st = self._b.pop(bid, None)
        if st:
            try:
                await self._r.delete(K_CLIENTS.format(bid))
            except Exception:  # noqa: BLE001
                pass

    async def summary(self, db, b: Broadcast, *, save: bool = True) -> dict:
        samples = [r.data for r in (await db.execute(select(LiveDiagSample).where(LiveDiagSample.broadcast_id == b.id).order_by(LiveDiagSample.at))).scalars()]
        events = list((await db.execute(select(LiveDiagEvent).where(LiveDiagEvent.broadcast_id == b.id))).scalars())
        room = await db.get(Room, b.room_id)
        rtmp = max((len(s.get("rtmp") or []) for s in samples), default=0)
        mode = {"hls": bool(b.hls_dir), "rtmp": rtmp, "cdn": any(s.get("cdn") for s in samples) or bool(room and room.live_cdn)}
        out = summarize(samples, events, b, version=getattr(self._s, "app_version", ""), mode=mode)
        if save:
            b.diag_summary = out
            await db.commit()
        return out

    async def retention(self, cfg: dict) -> None:
        """Сроки и объём: показатели и события старше `diag_keep_days`; показателей всего не больше MAX_SAMPLE_ROWS. Итог эфира остаётся для сравнения."""
        cut = datetime.now(timezone.utc) - timedelta(days=int(cfg.get("diag_keep_days") or 30))
        async with self._sm() as db:
            await db.execute(delete(LiveDiagSample).where(LiveDiagSample.at < cut))
            await db.execute(delete(LiveDiagEvent).where(LiveDiagEvent.at < cut))
            n = int((await db.execute(select(func.count()).select_from(LiveDiagSample))).scalar_one())
            if n > MAX_SAMPLE_ROWS:
                ids = select(LiveDiagSample.id).order_by(LiveDiagSample.id).limit(n - MAX_SAMPLE_ROWS)
                await db.execute(delete(LiveDiagSample).where(LiveDiagSample.id.in_(ids)))
            await db.commit()
            # эфиры, закрывшиеся, пока сбор не работал (перезапуск приложения): итог считается здесь
            rows = (await db.execute(select(Broadcast).where(Broadcast.diag_summary.is_(None), Broadcast.status.not_in(LIVE_ACTIVE), Broadcast.id.in_(select(LiveDiagSample.broadcast_id).distinct())).limit(20))).scalars().all()
            for b in rows:
                await self.summary(db, b)

    # ---------------------------------------------------------------- цикл
    async def _cfg(self) -> dict:
        async with self._sm() as db:
            c = await self._svc.get(db, "live")
        return c.model_dump() if hasattr(c, "model_dump") else dict(c)

    async def run(self) -> None:
        tick = 0
        while True:
            try:
                wait = await self._cycle(tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — сбор показателей не должен останавливаться из-за разового сбоя
                log.exception("Сбой сбора показателей эфира")
                wait = TICK_S
            tick += 1
            t0 = time.perf_counter()
            await asyncio.sleep(wait)
            self._lag_ms = round(max(0.0, time.perf_counter() - t0 - wait) * 1000, 1)        # насколько позже срока проснулся цикл: признак перегруженного приложения

    async def _cycle(self, tick: int) -> float:
        now = time.time()
        async with self._sm() as db:
            rows = list((await db.execute(select(Broadcast).where(Broadcast.status.in_(LIVE_ACTIVE)))).scalars())
            ctx = [(b, await db.get(Room, b.room_id), await db.get(Meeting, b.meeting_id)) for b in rows]
        cfg = await self._cfg()
        if now - self._retained > RETENTION_EVERY_S:
            self._retained = now
            await self.retention(cfg)
        for bid in list(self._b):
            if bid not in {b.id for b in rows}:
                await self.finish(bid)
        if not rows or not cfg.get("diag_enabled", True):
            self._cores, self._disk, self._net = {}, None, None       # после простоя скорости считаются заново, а не за всё время простоя
            self._starts = {}                                         # служба, перезапущенная в простое, не должна выглядеть сбоем следующего эфира
            return IDLE_S
        ldir = self.live.live_dir()
        host = await asyncio.to_thread(self.sample_host, now, ldir, cfg)
        self._host = host
        slow = tick % SLOW_TICKS == 0 or not self._svc_view
        restarted: list[str] = []
        if slow:
            self._svc_view, restarted = await self.sample_services(cfg, ldir)
        for b, room, meeting in ctx:
            st = self._b.get(b.id)
            if st is None or "findings" not in st:
                st = await self._begin(b, room, meeting)
            base = {"t": round(now, 1), "bid": str(b.id)}
            if slow:
                s = await self._slow(b, room, meeting, st, cfg, {**base, "host": host}, self._svc_view, restarted, now)
                st["last"] = s
                await self._store(b.id, s, ring=True)
                if now - st.get("last_persist", 0) >= PERSIST_S:
                    st["last_persist"] = now
                    await self._persist(b.id, st, s, (host.get("disk") or {}).get("free"))
            elif st.get("last"):
                await self._store(b.id, {**st["last"], "t": round(now, 1), "host": host}, ring=False)
        return TICK_S

    # ---------------------------------------------------------------- чтение (API)
    async def current(self, bid: uuid.UUID, *, ring: bool = True) -> dict:
        out: dict[str, Any] = {"now": None, "ring": [], "keys": RING_KEYS}
        try:
            raw = await self._r.get(K_NOW.format(bid))
            out["now"] = json.loads(raw) if raw else None
            if ring:
                out["ring"] = [json.loads(x) for x in await self._r.lrange(K_RING.format(bid), 0, -1)]
        except Exception:  # noqa: BLE001
            pass
        return out


def read_cgroup_cpu(path: Path) -> tuple[int, float, int] | None:
    """Накопленное время процессора Egress, момент записи файла и время, урезанное пределом контейнера; файл старше минуты — служба его не обновляет, данных нет."""
    try:
        st = path.stat()
        if time.time() - st.st_mtime > 60:
            return None
        vals = dict(line.split()[:2] for line in path.read_text().splitlines() if " " in line)
        return int(vals["usage_usec"]), st.st_mtime, int(vals.get("throttled_usec", 0))
    except (OSError, ValueError, KeyError):
        return None


__all__ = ["LiveDiag", "RING_KEYS", "Finding", "rules", "stage_view", "summarize", "clean", "clean_text", "clean_client", "hls_stats", "parse_playlist", "parse_probe", "aggregate_clients", "viewer_verdict"]
