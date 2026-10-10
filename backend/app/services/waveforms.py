"""Волновая форма аудиозаписей: компактный массив пиков громкости, который строится ОДИН РАЗ (при готовности записи или по первому запросу для старой записи) и хранится в базе
рядом с метаданными — браузер не анализирует аудио и не скачивает файл ради картинки.

Формат: по одному байту на «столбик» длиной `BUCKET_MS` (100 мс): 0 — тишина (≤ −60 дБ), 255 — полная шкала; шкала логарифмическая (дБ), чтобы речь была видна, а паузы — низкими.
Три часа записи = 108 000 байт. Пики считает ffmpeg (`astats`, окно в 100 мс на 8 кГц): ни Python-циклов по отсчётам, ни numpy. Источник — локальный файл либо внешнее хранилище
(сначала потоком копируется во временный файл, который удаляется), поэтому перенос записи между хранилищами волну не затрагивает: она привязана к записи, а не к файлу.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Recording, RecordingWaveform, utcnow
from . import mixdown
from .storage import StorageError

log = logging.getLogger("app.waveforms")

BUCKET_MS = 100
FLOOR_DB = -60.0
_LINE = re.compile(r"lavfi\.astats\.Overall\.Peak_level=(-?\d+(?:\.\d+)?|-inf|inf|nan)")


def db_to_byte(db: float) -> int:
    if db != db or db == -math.inf:
        return 0
    return max(0, min(255, round(255 * (db - FLOOR_DB) / -FLOOR_DB)))


def parse_peaks(lines) -> bytes:
    """Строки вывода ffmpeg (ametadata=print) → байты пиков."""
    out = bytearray()
    for line in lines:
        m = _LINE.search(line)
        if m:
            try:
                out.append(db_to_byte(float(m.group(1))))
            except ValueError:
                out.append(0)
    return bytes(out)


def compute_peaks(path: str | os.PathLike, *, timeout: float = 3600) -> bytes:
    """Блокирующий расчёт (запускать в потоке). Не изменяет файл; вывод читается построчно — в память целиком не попадает."""
    ff = mixdown.ffmpeg_path()
    if ff is None:
        raise RuntimeError("ffmpeg не найден: волновая форма не может быть построена")
    n = int(8000 * BUCKET_MS / 1000)
    cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-af",
           f"aresample=8000,asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.Peak_level:file=-", "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")   # noqa: S603
    try:
        peaks = parse_peaks(proc.stdout)                                                      # type: ignore[arg-type]
        err = proc.stderr.read() if proc.stderr else ""
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("ffmpeg не уложился во время при расчёте волновой формы") from None
    if rc != 0 and not peaks:
        raise RuntimeError(f"ffmpeg завершился с ошибкой: {err.strip()[:200]}")
    return peaks


class WaveformService:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession], protocols):
        self._sm, self._ps = session_maker, protocols
        self._running: set = set()
        self._tasks: set[asyncio.Task] = set()

    # --------------------------------------------------------------------------- чтение
    async def get(self, db: AsyncSession, rec_id) -> RecordingWaveform | None:
        return await db.get(RecordingWaveform, rec_id)

    @staticmethod
    def payload(w: RecordingWaveform) -> dict:
        return {"status": w.status, "bucket_ms": w.bucket_ms, "peaks": base64.b64encode(w.peaks).decode("ascii") if w.status == "ready" and w.peaks else "", "error": w.error}

    # --------------------------------------------------------------------------- построение
    def ensure(self, rec_id) -> bool:
        """Запустить построение в фоне, если оно ещё не идёт. True — запущено."""
        if rec_id in self._running:
            return False
        self._running.add(rec_id)
        task = asyncio.create_task(self._guarded(rec_id), name=f"waveform-{rec_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _guarded(self, rec_id) -> None:
        try:
            await self.build(rec_id)
        except Exception:  # noqa: BLE001 — фоновая задача не должна ронять приложение
            log.exception("Не удалось построить волновую форму", extra={"recording": str(rec_id)})
        finally:
            self._running.discard(rec_id)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def build(self, rec_id, *, force: bool = False) -> None:
        async with self._sm() as db:
            rec = await db.get(Recording, rec_id)
            if rec is None or rec.status != "ready":
                return
            w = await db.get(RecordingWaveform, rec_id)
            if w is not None and w.status == "ready" and not force:
                return
            if w is None:
                w = RecordingWaveform(recording_id=rec_id, status="processing", bucket_ms=BUCKET_MS, peaks=b"")
                db.add(w)
            else:
                w.status, w.error = "processing", None
            await db.commit()
            path = rec.path
        tmp: str | None = None
        try:
            root = Path(self._ps._s.recordings_path).resolve()
            full = (root / path).resolve()
            if root in full.parents and full.is_file():
                src = str(full)
            else:                                                    # запись во внешнем хранилище: потоком копируем во временный файл
                async with self._sm() as db:
                    rec = await db.get(Recording, rec_id)
                    storage = await self._ps._audio_storage(db) if rec.export_status == "exported" else None
                if storage is None:
                    raise StorageError("файл записи недоступен")
                fd, tmp = tempfile.mkstemp(prefix="wave-", suffix=Path(path).suffix or ".bin")
                os.close(fd)
                await asyncio.to_thread(storage.copy_out, path, tmp)
                src = tmp
            peaks = await asyncio.to_thread(compute_peaks, src)
            err, status = None, "ready"
            if not peaks:
                err, status = "в записи нет звука", "failed"
        except Exception as exc:  # noqa: BLE001
            peaks, status, err = b"", "failed", str(exc)[:290]
            log.warning("Волновая форма не построена: %s", exc, extra={"recording": str(rec_id)})
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)
        async with self._sm() as db:
            w = await db.get(RecordingWaveform, rec_id)
            if w is None:
                return                                               # запись удалена, пока считали
            w.status, w.peaks, w.error, w.bucket_ms, w.built_at = status, peaks, err, BUCKET_MS, utcnow()
            await db.commit()

    async def build_many(self, rec_ids) -> None:
        """Построить для набора записей (после сборки общей записи). Ошибки не пробрасываются."""
        for rid in rec_ids:
            try:
                await self.build(rid)
            except Exception:  # noqa: BLE001
                log.exception("Не удалось построить волновую форму", extra={"recording": str(rid)})
