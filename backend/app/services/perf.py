"""Производительность: лёгкий сбор показателей хоста и контейнера и защита звонков от тяжёлых фоновых задач.

Принципы:
  * сбор — только чтение /proc и cgroup (микросекунды), без внешних программ и без постоянно работающего опроса: CPU считается по разности
    двух чтений между обращениями (страница «Производительность» опрашивает раз в несколько секунд);
  * показатель, который получить нельзя (нет /proc, нет cgroup, служба не отвечает), — `None`, интерфейс показывает «Нет данных»;
  * `HeavyGate` — единая очередь тяжёлых фоновых задач (сведение общей записи, волновая форма, протокол): одновременно выполняется
    ограниченное число, а пока идёт встреча и сервер перегружен, запуск откладывается (но не дольше `max_defer`: работа не голодает).
    Звонок (LiveKit) и распознавание речи во время встречи имеют приоритет; сами они задерживаться не могут и не задерживаются.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

log = logging.getLogger("app.perf")

# Пороги уровней нагрузки: «занят» — фоновые задачи на время встреч лучше подождать, «перегружен» — ждут обязательно.
BUSY_LOAD_PER_CPU = 0.85       # load1 / число ядер
OVERLOAD_LOAD_PER_CPU = 1.4
BUSY_CPU_PCT = 80.0
OVERLOAD_CPU_PCT = 92.0
LOW_MEM_AVAILABLE_PCT = 10.0
ASR_QUEUE_BUSY = 8             # реплик в очереди распознавания: речь начинает запаздывать
ASR_QUEUE_OVERLOAD = 24


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def read_cpu_times(root: str = "/proc") -> tuple[int, int] | None:
    """(занято, всего) тиков процессора хоста из /proc/stat."""
    txt = _read(f"{root}/stat")
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith("cpu "):
            try:
                v = [int(x) for x in line.split()[1:9]]
            except ValueError:
                return None
            idle = v[3] + (v[4] if len(v) > 4 else 0)         # idle + iowait
            total = sum(v)
            return total - idle, total
    return None


def read_meminfo(root: str = "/proc") -> dict[str, int] | None:
    txt = _read(f"{root}/meminfo")
    if not txt:
        return None
    mem: dict[str, int] = {}
    for line in txt.splitlines():
        k, _, v = line.partition(":")
        try:
            mem[k] = int(v.split()[0]) * 1024
        except (ValueError, IndexError):
            continue
    return mem or None


def read_loadavg(root: str = "/proc") -> tuple[float, float, float] | None:
    txt = _read(f"{root}/loadavg")
    try:
        a = txt.split()[:3] if txt else []
        return float(a[0]), float(a[1]), float(a[2])
    except (ValueError, IndexError):
        return None


def read_cgroup(root: str = "/sys/fs/cgroup") -> dict[str, Any] | None:
    """Ресурсы ЭТОГО контейнера (cgroup v2; на v1 — «Нет данных»): накопленное время CPU (мкс), память, лимит памяти."""
    cpu = _read(f"{root}/cpu.stat")
    mem = _read(f"{root}/memory.current")
    if cpu is None and mem is None:
        return None
    out: dict[str, Any] = {"cpu_usec": None, "mem_bytes": None, "mem_limit": None}
    if cpu:
        for line in cpu.splitlines():
            if line.startswith("usage_usec"):
                with contextlib.suppress(ValueError, IndexError):
                    out["cpu_usec"] = int(line.split()[1])
    if mem:
        with contextlib.suppress(ValueError):
            out["mem_bytes"] = int(mem.strip())
    lim = _read(f"{root}/memory.max")
    if lim and lim.strip().isdigit():
        out["mem_limit"] = int(lim.strip())
    return out


@dataclass
class HostSample:
    cpu_pct: float | None
    cpus: int
    load1: float | None
    load5: float | None
    load15: float | None
    mem_total: int | None
    mem_available: int | None
    container_cpu_pct: float | None
    container_mem: int | None
    container_mem_limit: int | None
    at: float = field(default_factory=time.time)

    @property
    def mem_available_pct(self) -> float | None:
        return round(100.0 * self.mem_available / self.mem_total, 1) if self.mem_total and self.mem_available is not None else None

    def as_dict(self) -> dict:
        return {
            "cpu_pct": self.cpu_pct, "cpus": self.cpus, "load1": self.load1, "load5": self.load5, "load15": self.load15,
            "mem_total": self.mem_total, "mem_available": self.mem_available, "mem_used_pct": None if self.mem_available_pct is None else round(100 - self.mem_available_pct, 1),
            "container": {"cpu_pct": self.container_cpu_pct, "mem_bytes": self.container_mem, "mem_limit": self.container_mem_limit},
        }


class HostMonitor:
    """Показатели хоста. CPU — по разности двух чтений; повторные вызовы чаще `min_interval` возвращают прошлый результат (опрос не нагружает сервер)."""

    def __init__(self, proc_root: str = "/proc", cgroup_root: str = "/sys/fs/cgroup", min_interval: float = 1.5) -> None:
        self._proc, self._cg, self._min = proc_root, cgroup_root, min_interval
        self._prev_cpu: tuple[int, int] | None = None
        self._prev_cg: tuple[float, int] | None = None
        self._last: HostSample | None = None

    def sample(self) -> HostSample:
        now = time.time()
        if self._last is not None and now - self._last.at < self._min:
            return self._last
        cpu_pct = None
        cur = read_cpu_times(self._proc)
        if cur is not None:
            if self._prev_cpu is not None and cur[1] > self._prev_cpu[1]:
                cpu_pct = round(100.0 * (cur[0] - self._prev_cpu[0]) / (cur[1] - self._prev_cpu[1]), 1)
            self._prev_cpu = cur
        cg = read_cgroup(self._cg)
        cg_pct = None
        if cg and cg["cpu_usec"] is not None:
            if self._prev_cg is not None and now > self._prev_cg[0]:
                cg_pct = round(100.0 * (cg["cpu_usec"] - self._prev_cg[1]) / 1e6 / (now - self._prev_cg[0]), 1)      # 100 % = одно ядро
            self._prev_cg = (now, cg["cpu_usec"])
        mem, load = read_meminfo(self._proc), read_loadavg(self._proc)
        self._last = HostSample(
            cpu_pct=cpu_pct, cpus=os.cpu_count() or 1,
            load1=load[0] if load else None, load5=load[1] if load else None, load15=load[2] if load else None,
            mem_total=mem.get("MemTotal") if mem else None, mem_available=mem.get("MemAvailable") if mem else None,
            container_cpu_pct=cg_pct, container_mem=cg["mem_bytes"] if cg else None, container_mem_limit=cg["mem_limit"] if cg else None, at=now)
        return self._last


def assess(host: HostSample, *, asr_queue: int | None = None, active_meetings: int = 0) -> dict:
    """Уровень нагрузки и причины простым языком: ok | busy | overloaded. Основа для откладывания фоновых задач и для страницы «Производительность»."""
    level = 0
    reasons: list[str] = []

    def bump(lv: int, text: str) -> None:
        nonlocal level
        level = max(level, lv)
        reasons.append(text)

    per_cpu = (host.load1 / host.cpus) if host.load1 is not None and host.cpus else None
    if per_cpu is not None:
        if per_cpu >= OVERLOAD_LOAD_PER_CPU:
            bump(2, f"очередь на процессор: нагрузка {host.load1:.1f} на {host.cpus} ядер")
        elif per_cpu >= BUSY_LOAD_PER_CPU:
            bump(1, f"высокая нагрузка процессора: {host.load1:.1f} на {host.cpus} ядер")
    if host.cpu_pct is not None:
        if host.cpu_pct >= OVERLOAD_CPU_PCT:
            bump(2, f"процессор занят на {host.cpu_pct:.0f} %")
        elif host.cpu_pct >= BUSY_CPU_PCT:
            bump(1, f"процессор занят на {host.cpu_pct:.0f} %")
    if host.mem_available_pct is not None and host.mem_available_pct < LOW_MEM_AVAILABLE_PCT:
        bump(2 if host.mem_available_pct < LOW_MEM_AVAILABLE_PCT / 2 else 1, f"мало свободной памяти: {host.mem_available_pct:.0f} %")
    if asr_queue is not None:
        if asr_queue >= ASR_QUEUE_OVERLOAD:
            bump(2, f"очередь распознавания речи: {asr_queue} реплик — стенограмма запаздывает")
        elif asr_queue >= ASR_QUEUE_BUSY:
            bump(1, f"очередь распознавания речи: {asr_queue} реплик")
    return {"level": ("ok", "busy", "overloaded")[level], "reasons": reasons, "active_meetings": active_meetings}


@dataclass
class GateStats:
    running: int = 0
    waiting: int = 0
    started: int = 0
    deferred: int = 0              # сколько запусков пришлось отложить из-за нагрузки
    deferred_seconds: float = 0.0
    forced: int = 0                # запусков после предельного ожидания (не голодаем)
    recent: deque = field(default_factory=lambda: deque(maxlen=20))


class HeavyGate:
    """Очередь тяжёлых фоновых задач. `async with gate.slot("mix", defer_at="busy"):` — тело выполняется, когда есть свободное место и
    (при идущих встречах) сервер не перегружен выше `defer_at`. Ожидание ограничено `max_defer` секунд, потом задача идёт всё равно."""

    LEVELS = {"ok": 0, "busy": 1, "overloaded": 2}

    def __init__(self, status: Callable[[], Awaitable[dict]], *, concurrency: int = 2, poll: float = 10.0, max_defer: float = 1800.0) -> None:
        self._status = status
        self._sem = asyncio.Semaphore(concurrency)
        self._poll, self._max_defer = poll, max_defer
        self.concurrency = concurrency
        self.stats = GateStats()

    @contextlib.asynccontextmanager
    async def slot(self, kind: str, *, defer_at: str = "busy", max_defer: float | None = None) -> AsyncIterator[None]:
        st = self.stats
        st.waiting += 1
        waited_since = time.monotonic()
        deferred = False
        limit = self._max_defer if max_defer is None else max_defer
        try:
            while True:
                try:
                    s = await self._status()
                except Exception:  # noqa: BLE001 — не смогли узнать нагрузку: не задерживаем
                    s = {"level": "ok", "active_meetings": 0}
                busy = s.get("active_meetings", 0) > 0 and self.LEVELS.get(s.get("level", "ok"), 0) >= self.LEVELS[defer_at]
                if not busy:
                    break
                if time.monotonic() - waited_since >= limit:
                    st.forced += 1
                    break
                deferred = True
                await asyncio.sleep(self._poll)
            await self._sem.acquire()
        finally:
            st.waiting -= 1
        if deferred:
            st.deferred += 1
            st.deferred_seconds += time.monotonic() - waited_since
            log.info("Тяжёлая фоновая задача отложена из-за нагрузки", extra={"kind": kind, "seconds": round(time.monotonic() - waited_since)})
        st.running += 1
        st.started += 1
        t0 = time.monotonic()
        try:
            yield
        finally:
            st.running -= 1
            self._sem.release()
            st.recent.append({"kind": kind, "waited_s": round(t0 - waited_since, 1), "ran_s": round(time.monotonic() - t0, 1), "deferred": deferred, "at": time.time()})

    def snapshot(self) -> dict:
        s = self.stats
        return {"concurrency": self.concurrency, "running": s.running, "waiting": s.waiting, "started": s.started, "deferred": s.deferred,
                "deferred_seconds": round(s.deferred_seconds), "forced": s.forced, "recent": list(s.recent)[-5:]}


def parse_prometheus(text: str, names: dict[str, str]) -> dict[str, list[tuple[dict, float]]]:
    """Минимальный разбор текстового формата Prometheus: {имя: [(метки, значение)]} только для запрошенных метрик (names: имя → ключ)."""
    out: dict[str, list[tuple[dict, float]]] = {}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        for metric, key in names.items():
            if line.startswith(metric) and len(line) > len(metric) and line[len(metric)] in " {":
                labels: dict[str, str] = {}
                rest = line[len(metric):]
                if rest.startswith("{"):
                    end = rest.index("}")
                    for part in rest[1:end].split(","):
                        k, _, v = part.partition("=")
                        labels[k.strip()] = v.strip().strip('"')
                    rest = rest[end + 1:]
                try:
                    out.setdefault(key, []).append((labels, float(rest.split()[0])))
                except (ValueError, IndexError):
                    pass
                break
    return out


class RateTracker:
    """Скорость счётчика (в секунду) по двум последним чтениям."""

    def __init__(self) -> None:
        self._prev: dict[str, tuple[float, float]] = {}

    def rate(self, key: str, value: float | None, now: float | None = None) -> float | None:
        if value is None:
            return None
        now = time.time() if now is None else now
        prev = self._prev.get(key)
        self._prev[key] = (now, value)
        if prev is None or now <= prev[0] or value < prev[1]:
            return None
        return (value - prev[1]) / (now - prev[0])


class PerfHub:
    """Состояние сбора показателей приложения: монитор хоста, очередь тяжёлых задач и кэш обращений к LiveKit (не чаще раза в `ttl` секунд)."""

    def __init__(self, settings, redis, session_maker, *, ttl: float = 5.0) -> None:
        self._s, self._r, self._sm, self._ttl = settings, redis, session_maker, ttl
        self.host = HostMonitor()
        self.rates = RateTracker()
        self.gate = HeavyGate(self.status, concurrency=max(1, int(getattr(settings, "heavy_concurrency", 2))),
                              max_defer=float(getattr(settings, "heavy_max_defer_seconds", 1800)))
        self._meetings: tuple[float, int] = (0.0, 0)
        self._status: tuple[float, dict] = (0.0, {"level": "ok", "reasons": [], "active_meetings": 0})
        self._lk: tuple[float, dict] = (0.0, {})

    async def _active_meetings(self) -> int:
        now = time.monotonic()
        if now - self._meetings[0] < 10.0:
            return self._meetings[1]
        from sqlalchemy import func, select  # noqa: PLC0415

        from ..models import Meeting  # noqa: PLC0415

        try:
            async with self._sm() as db:
                n = int((await db.execute(select(func.count()).select_from(Meeting).where(Meeting.ended_at.is_(None)))).scalar_one())
        except Exception:  # noqa: BLE001
            n = self._meetings[1]
        self._meetings = (now, n)
        return n

    async def _asr_queue(self) -> int | None:
        try:
            raw = await self._r.get("asr:heartbeat")
        except Exception:  # noqa: BLE001
            return None
        if not raw:
            return None
        import json  # noqa: PLC0415

        try:
            q = json.loads(raw).get("queue_depth")
            return int(q) if q is not None else None
        except (ValueError, TypeError, AttributeError):
            return None

    async def status(self) -> dict:
        """Уровень нагрузки сейчас (кэш 5 с): ok | busy | overloaded, причины, число идущих встреч."""
        now = time.monotonic()
        if now - self._status[0] < self._ttl:
            return self._status[1]
        res = assess(self.host.sample(), asr_queue=await self._asr_queue(), active_meetings=await self._active_meetings())
        self._status = (now, res)
        return res

    async def livekit(self) -> dict:
        """Сервер звонков: комнаты, участники, дорожки и трафик. Источники — API LiveKit и его метрики Prometheus (если включены); недоступное — None."""
        now = time.monotonic()
        if now - self._lk[0] < self._ttl and self._lk[1]:
            return self._lk[1]
        out: dict[str, Any] = {"available": False, "rooms": None, "participants": None, "publishers": None, "audio_tracks": None, "video_tracks": None,
                               "in_kbps": None, "out_kbps": None, "dropped_pps": None, "metrics": False}
        try:
            from livekit import api as lkapi  # noqa: PLC0415

            async with lkapi.LiveKitAPI(self._s.livekit_http_url, self._s.livekit_api_key, self._s.livekit_api_secret) as lk:
                resp = await asyncio.wait_for(lk.room.list_rooms(lkapi.ListRoomsRequest()), timeout=3.0)
            rooms = [r for r in resp.rooms]
            out.update(available=True, rooms=len(rooms), participants=sum(int(r.num_participants) for r in rooms), publishers=sum(int(r.num_publishers) for r in rooms))
        except Exception as exc:  # noqa: BLE001
            out["error"] = type(exc).__name__
        text = await self._metrics_text()
        if text is not None:
            m = parse_prometheus(text, {"livekit_packet_bytes": "bytes", "livekit_node_packet_total": "pkts", "livekit_track_published_total": "pub",
                                        "livekit_track_subscribed_total": "sub", "livekit_participant_total": "participants", "livekit_room_total": "rooms",
                                        "livekit_nack_total": "nack", "livekit_pli_total": "pli", "livekit_forward_jitter": "jitter"})
            out["metrics"] = True
            t = time.time()
            for direction, key in (("incoming", "in_kbps"), ("outgoing", "out_kbps")):
                total = sum(v for lab, v in m.get("bytes", []) if lab.get("direction") == direction)
                if m.get("bytes"):
                    r = self.rates.rate(f"lk_bytes_{direction}", total, t)
                    out[key] = None if r is None else round(r * 8 / 1000, 1)
            if m.get("pkts"):
                dropped = sum(v for lab, v in m["pkts"] if lab.get("type") == "dropped")
                r = self.rates.rate("lk_dropped", dropped, t)
                out["dropped_pps"] = None if r is None else round(r, 1)
            for kind in ("audio", "video"):
                vals = [v for lab, v in m.get("pub", []) if lab.get("kind") == kind]
                if vals:
                    out[f"{kind}_tracks"] = int(sum(vals))
            if m.get("jitter"):
                out["forward_jitter_ms"] = round(max(v for _, v in m["jitter"]) / 1e6, 2) if max(v for _, v in m["jitter"]) > 1000 else round(max(v for _, v in m["jitter"]), 2)
        self._lk = (now, out)
        return out

    async def _metrics_text(self) -> str | None:
        url = getattr(self._s, "livekit_metrics_url", "") or ""
        if not url:
            from urllib.parse import urlsplit  # noqa: PLC0415

            u = urlsplit(self._s.livekit_http_url)
            url = f"{u.scheme}://{u.hostname}:6789/metrics" if u.hostname else ""
        if not url:
            return None
        try:
            import httpx  # noqa: PLC0415

            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(url)
            return r.text if r.status_code == 200 else None
        except Exception:  # noqa: BLE001
            return None
