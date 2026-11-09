"""Ретрансляция потока на внешние площадки отдельными процессами ffmpeg.

Зачем: если площадки подключены прямо к Egress, одна из них, рвущая соединение раз за разом, через десятки секунд замораживает весь его конвейер: встают сегменты HLS,
запись и остальные площадки (воспроизведено на Egress v1.15, и снятие площадки уже после зависания не помогает). Здесь каждая площадка — свой процесс `ffmpeg -c copy`,
который читает локальный поток из MediaMTX: сбой, зависание и повторы одной площадки не касаются ни Egress, ни других площадок, а паузы между попытками задаём мы.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger("relay")

BACKOFF = (2.0, 4.0, 8.0, 15.0, 30.0)     # паузы между попытками: растут, пока площадка не принимает поток
QUICK_FAIL_S = 10.0                       # процесс прожил меньше — попытка неудачная
STABLE_S = 60.0                           # прожил дольше — счётчик неудач сбрасывается
STALL_S = 20.0                            # нет ни одного отправленного байта — площадка зависла, процесс убирается


def secrets_of(url: str) -> set[str]:
    """Что нельзя показывать в ошибках: весь адрес, ключ (всё после последней «/») и его части."""
    tail = url.rsplit("/", 1)[-1]
    return {s for s in {url, tail, *re.split(r"[?&=]", tail)} if len(s) >= 6}


def scrub(text: str, secrets: set[str]) -> str:
    for s in sorted(secrets, key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def fingerprint(src: str, url: str, audio: bool = False, low: bool = False) -> str:
    return hashlib.sha256(f"{src}\n{url}\n{int(audio)}{int(low)}".encode()).hexdigest()[:12]


@dataclass
class Relay:
    id: str
    src: str
    url: str
    audio: bool = False                   # только звук: дорожка для зрителей со слабой связью (ffmpeg -vn -c:a copy, без перекодирования)
    low: bool = False                     # низкое качество (360p): облегчённое видео для зрителей, которым не хватает канала; единственный процесс с перекодированием
    state: str = "starting"               # starting — первый запуск, live — данные идут, retrying — пауза или повтор после сбоя
    restarts: int = 0
    fails: int = 0                        # подряд неудачных попыток
    bytes_out: int = 0
    live_since: float | None = None
    last_error: str = ""
    last_progress: float = 0.0
    task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {"id": self.id, "state": self.state, "restarts": self.restarts, "fails": self.fails, "bytes": self.bytes_out, "since": self.live_since,
                "error": self.last_error, "fp": fingerprint(self.src, self.url, self.audio, self.low), "audio": self.audio, "low": self.low}


class RelayManager:
    def __init__(self, ffmpeg: str = "ffmpeg", backoff: tuple[float, ...] = BACKOFF, stall_s: float = STALL_S):
        self.ffmpeg, self.backoff, self.stall_s = ffmpeg, backoff, stall_s
        self.relays: dict[str, Relay] = {}

    def put(self, rid: str, src: str, url: str, audio: bool = False, low: bool = False) -> None:
        cur = self.relays.get(rid)
        if cur is not None and cur.src == src and cur.url == url and cur.audio == audio and cur.low == low and cur.task is not None and not cur.task.done():
            return
        if cur is not None and cur.task is not None:
            cur.task.cancel()               # заменённый адрес или ключ: прежний процесс завершается сам в finally
        r = Relay(rid, src, url, audio, low)
        r.task = asyncio.get_running_loop().create_task(self._run(r))
        self.relays[rid] = r

    async def delete(self, rid: str) -> bool:
        r = self.relays.pop(rid, None)
        if r is None or r.task is None:
            return False
        r.task.cancel()
        await asyncio.gather(r.task, return_exceptions=True)
        return True

    def status(self) -> list[dict]:
        return [r.public() for r in self.relays.values()]

    async def close(self) -> None:
        for rid in list(self.relays):
            await self.delete(rid)

    # ------------------------------------------------------------------------------------------------ процесс одной площадки
    def command(self, r: Relay) -> list[str]:
        # Низкое качество: единственный процесс с перекодированием (360p, ключевой кадр раз в секунду — по ним MediaMTX режет сегменты). Работает с пониженным приоритетом и
        # в два потока: нехватка процессора у него не должна отнимать время у Egress, записи и распознавания.
        if r.low:
            codec = ["-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-profile:v", "main", "-threads", "2",
                     "-b:v", "450k", "-maxrate", "550k", "-bufsize", "900k", "-force_key_frames", "expr:gte(t,n_forced*1)", "-sc_threshold", "0", "-c:a", "copy"]
        else:
            codec = ["-vn", "-map", "0:a", "-c:a", "copy"] if r.audio else ["-map", "0", "-c", "copy"]
        return [*(["nice", "-n", "10"] if r.low else []), self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1",
                "-rw_timeout", "10000000", "-analyzeduration", "1000000", "-probesize", "500000", "-rtmp_live", "live", "-i", r.src,
                *codec, "-rw_timeout", "10000000", "-f", "flv", "-flvflags", "no_duration_filesize", r.url]

    async def _run(self, r: Relay) -> None:
        try:
            while True:
                t0 = time.monotonic()
                try:
                    await self._once(r)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — сбой запуска процесса не должен останавливать повторы
                    r.last_error = type(exc).__name__
                alive = time.monotonic() - t0
                if alive >= STABLE_S:
                    r.fails = 0
                elif alive < QUICK_FAIL_S:
                    r.fails += 1
                r.restarts += 1
                r.state = "retrying"
                await asyncio.sleep(self.backoff[min(r.fails, len(self.backoff) - 1)])
        finally:
            r.state = "retrying" if r.state != "live" else r.state

    async def _once(self, r: Relay) -> None:
        secrets = secrets_of(r.url)
        proc = await asyncio.create_subprocess_exec(*self.command(r), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        r.last_progress = time.monotonic()
        tail: deque[str] = deque(maxlen=4)

        async def out() -> None:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("total_size="):
                    n = int(line.split("=", 1)[1] or 0) if line.split("=", 1)[1].isdigit() else 0
                    if n > r.bytes_out:
                        r.bytes_out, r.last_progress = n, time.monotonic()
                        if r.state != "live":
                            r.state, r.live_since, r.last_error = "live", time.time(), ""
                    elif n == 0:
                        r.bytes_out = 0

        async def err() -> None:
            assert proc.stderr is not None
            async for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    tail.append(scrub(line, secrets)[:200])

        readers = [asyncio.create_task(out()), asyncio.create_task(err())]
        stalled = False
        try:
            while True:
                try:
                    await asyncio.wait_for(proc.wait(), 1.0)
                    break
                except asyncio.TimeoutError:
                    if time.monotonic() - r.last_progress > self.stall_s:
                        stalled = True
                        proc.kill()
                        await proc.wait()
                        break
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            for t in readers:
                t.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
        r.bytes_out = 0 if r.state != "live" else r.bytes_out
        r.state = "retrying"
        r.live_since = None
        if stalled:
            r.last_error = "площадка не принимает данные"
        else:
            r.last_error = (tail[-1] if tail else f"процесс завершился с кодом {proc.returncode}")
