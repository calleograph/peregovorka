"""Общая аудиозапись встречи: файлы участников совмещаются по ВРЕМЕНИ и сводятся в один M4A (AAC).

Каждый файл участника — непрерывная дорожка (ASR-рекордер заполняет пропуски тишиной) с отметкой времени начала (`.t0`, настенные часы сервера). Сведение выполняет ffmpeg:
`adelay` сдвигает каждую дорожку на её смещение от начала встречи, `amix` (normalize=0) складывает голоса без искусственного ослабления, `alimiter` не даёт сумме одновременной речи
выйти за предел и исказиться. Файлы читаются самим ffmpeg потоком — память backend не растёт с длиной встречи. Итог создаётся один раз и далее только отдаётся для воспроизведения.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("app.mixdown")

MIME = "audio/mp4"
EXT = ".m4a"
BITRATE = "56k"
TIMEOUT_PER_HOUR_S = 900          # запас на сведение: десятки секунд на час звука, но считаем с большим запасом


class MixError(Exception):
    pass


@dataclass
class Track:
    path: Path
    t0: float | None               # настенное время начала дорожки (None — отметки нет: считаем началом встречи)


@dataclass
class MixPlan:
    start: float                   # настенное время 0:00 общей записи
    items: list[tuple[Path, int]]  # (файл, задержка в миллисекундах от начала)


def ffmpeg_path() -> str | None:
    want = os.environ.get("FFMPEG_BIN", "ffmpeg")
    return shutil.which(want) or (want if Path(want).is_file() else None)


def plan(tracks: list[Track]) -> MixPlan:
    """Смещения дорожек: от самого раннего начала. Дорожка без отметки времени ставится в начало (так мы не выдумываем сдвиг, которого не знаем)."""
    known = [t.t0 for t in tracks if t.t0 is not None]
    start = min(known) if known else 0.0
    return MixPlan(start=start, items=[(t.path, int(round(((t.t0 - start) if t.t0 is not None else 0.0) * 1000))) for t in tracks])


def build_command(ffmpeg: str, p: MixPlan, out: Path) -> list[str]:
    """Команда ffmpeg: входы — WAV участников, фильтр — сдвиг + сумма + ограничитель. Пути передаются аргументами (не через оболочку)."""
    if not p.items:
        raise MixError("Нет дорожек для сведения")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    for path, _ in p.items:
        cmd += ["-i", str(path)]
    parts, labels = [], []
    for i, (_, delay) in enumerate(p.items):
        parts.append(f"[{i}:a]aresample=16000,aformat=sample_fmts=fltp:channel_layouts=mono,adelay={delay}:all=1[a{i}]")
        labels.append(f"[a{i}]")
    if len(p.items) == 1:
        graph = parts[0].replace("[a0]", "[m]") + ";[m]alimiter=limit=0.85[out]"
    else:
        graph = ";".join(parts) + ";" + "".join(labels) + f"amix=inputs={len(p.items)}:duration=longest:normalize=0:dropout_transition=0,alimiter=limit=0.85[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-ac", "1", "-c:a", "aac", "-b:a", BITRATE, "-movflags", "+faststart", str(out)]
    return cmd


_DUR = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def duration_of(ffmpeg: str, path: Path) -> int:
    """Длительность готового файла: ffmpeg -i без вывода пишет её в stderr (отдельный ffprobe не нужен)."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, timeout=300, check=False)   # noqa: S603
    times = _DUR.findall(r.stderr or "")
    if not times:
        return 0
    h, m, s = times[-1]
    return int(float(h) * 3600 + float(m) * 60 + float(s))


def mix(tracks: list[Track], out: Path, *, max_seconds: float = 12 * 3600) -> tuple[MixPlan, int]:
    """Свести дорожки в `out` (M4A). Возвращает план и длительность в секундах. Блокирующая функция — запускать в потоке."""
    ff = ffmpeg_path()
    if ff is None:
        raise MixError("ffmpeg не найден: общая аудиозапись не может быть сформирована")
    p = plan(tracks)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    cmd = build_command(ff, p, tmp)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(600, TIMEOUT_PER_HOUR_S * max_seconds / 3600), check=False)   # noqa: S603
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        raise MixError("Сведение записи заняло слишком много времени") from None
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        log.error("ffmpeg завершился с ошибкой", extra={"code": r.returncode, "stderr": (r.stderr or "")[-400:]})
        raise MixError("Не удалось свести запись (ошибка ffmpeg)")
    os.replace(tmp, out)
    return p, duration_of(ff, out)
