"""Запись аудио: ASR-воркер пишет сырой PCM (int16, 16 кГц, моно) по файлу на участника —
`<recordings>/<livekit_room>/<identity>.pcm`. Формат без заголовка устойчив к аварийному обрыву.
По завершении встречи backend превращает файлы в WAV и раскладывает по структуре
`<комната>/<дата, день недели>/<время>/<участник>.wav`.
"""
from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .storage import safe_component

SAMPLE_RATE = 16000
BYTES_PER_SEC = SAMPLE_RATE * 2
MIN_SECONDS = 1.0  # короче — не сохраняем (случайные щелчки)


def wav_header(data_len: int) -> bytes:
    return b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, BYTES_PER_SEC, 2, 16) \
        + b"data" + struct.pack("<I", data_len)


@dataclass
class FinishedRecording:
    identity: str
    rel_path: str  # относительно каталога записей
    size_bytes: int
    duration_s: int


def finalize_pcm_files(recordings_dir: str, livekit_room: str, rel_dir: str, names: dict[str, str]) -> list[FinishedRecording]:
    """Блокирующая функция (запускать в потоке). names: identity -> отображаемое имя."""
    src_dir = Path(recordings_dir) / safe_component(livekit_room, "meeting")
    if not src_dir.is_dir():
        return []
    out_dir = Path(recordings_dir) / rel_dir
    done: list[FinishedRecording] = []
    for pcm in sorted(src_dir.glob("*.pcm")):
        size = pcm.stat().st_size - (pcm.stat().st_size % 2)
        identity = pcm.stem
        if size < BYTES_PER_SEC * MIN_SECONDS:
            pcm.unlink(missing_ok=True)
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        base = safe_component(names.get(identity, identity), identity)
        target = out_dir / f"{base}.wav"
        if target.exists():
            target = out_dir / f"{base} ({identity[-6:]}).wav"
        tmp = target.with_suffix(".wav.tmp")
        with open(pcm, "rb") as src, open(tmp, "wb") as dst:
            dst.write(wav_header(size))
            remaining = size
            while remaining:
                chunk = src.read(min(1 << 20, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
        os.replace(tmp, target)
        pcm.unlink(missing_ok=True)
        done.append(FinishedRecording(identity, str(target.relative_to(recordings_dir)).replace("\\", "/"),
                                      target.stat().st_size, int(size / BYTES_PER_SEC)))
    try:
        src_dir.rmdir()
    except OSError:
        pass
    return done


def delete_recording_file(recordings_dir: str, rel_path: str) -> bool:
    root = Path(recordings_dir).resolve()
    full = (root / rel_path).resolve()
    if root not in full.parents:
        return False
    full.unlink(missing_ok=True)
    for parent in list(full.parents)[:3]:  # убираем опустевшие каталоги даты/времени/комнаты
        if parent == root:
            break
        try:
            parent.rmdir()
        except OSError:
            break
    return True


def disk_free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def local_dir_for(room_name: str, start_local: datetime) -> str:
    from .storage import meeting_relpath

    return meeting_relpath(room_name, start_local)
