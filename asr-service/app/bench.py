"""Бенчмарк распознавания: выбор ASR_CPU_THREADS по ЗАДЕРЖКЕ, а не по загрузке CPU.

Запуск (внутри работающего контейнера asr или scripts/asr-bench.sh):
    python -m app.bench --wav /path/speech.wav --threads 2,4 --interop 1 --repeat 5

Для каждого числа потоков печатается средняя/p95 задержка распознавания одного фрагмента и RTF
(время распознавания / длительность аудио; меньше 1 — быстрее реального времени, чем меньше, тем запас больше).
Берите РЕАЛЬНУЮ русскую речь (--wav, 16 кГц моно или любой WAV — будет приведён). Режим --synthetic использует шум и
годится лишь для проверки, что скрипт работает: на нём время распознавания нерепрезентативно.
Пока бенчмарк идёт, не нагружайте сервер другими задачами, иначе числа будут завышены.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
import wave

import numpy as np

from .config import AsrSettings
from .providers.base import SAMPLE_RATE


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        n, ch, width, rate = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if width != 2:
        raise SystemExit("Ожидается 16-битный PCM WAV")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != SAMPLE_RATE:  # простая передискретизация линейной интерполяцией: для бенчмарка достаточно
        t = np.linspace(0, len(x) - 1, int(len(x) * SAMPLE_RATE / rate))
        x = np.interp(t, np.arange(len(x)), x)
    return x.astype(np.int16)


def run(provider, pcm: np.ndarray, repeat: int) -> dict:
    audio_s = len(pcm) / SAMPLE_RATE
    provider.transcribe(pcm)  # прогрев (первый вызов медленнее)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        provider.transcribe(pcm)
        times.append(time.perf_counter() - t0)
    avg = statistics.mean(times)
    return {"audio_s": round(audio_s, 2), "avg_ms": round(avg * 1000), "p95_ms": round(sorted(times)[min(len(times) - 1, int(len(times) * 0.95))] * 1000),
            "rtf": round(avg / audio_s, 3)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wav", help="WAV с речью (рекомендуется 5–15 секунд)")
    ap.add_argument("--synthetic", action="store_true", help="шум вместо речи (только проверка работоспособности)")
    ap.add_argument("--threads", default="2,4", help="варианты ASR_CPU_THREADS через запятую (по умолчанию 2,4)")
    ap.add_argument("--interop", type=int, default=1, help="ASR_INTEROP_THREADS для всех вариантов (по умолчанию 1)")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--seconds", type=float, default=8.0, help="длина синтетического сигнала")
    a = ap.parse_args(argv)
    if not a.wav and not a.synthetic:
        ap.error("укажите --wav с реальной речью (или --synthetic для проверки скрипта)")
    pcm = load_wav(a.wav) if a.wav else (np.random.default_rng(1).normal(0, 2500, int(a.seconds * SAMPLE_RATE))).astype(np.int16)

    from .providers.gigaam import GigaAmProvider  # noqa: PLC0415

    s = AsrSettings()
    rows = []
    first = True
    for n in [int(x) for x in a.threads.split(",") if x.strip()]:
        p = GigaAmProvider(s.asr_model_name, s.asr_model_dir, s.asr_device, n, a.interop if first else 0)
        p.load()  # применяет потоки и прогревает
        first = False  # inter-op пул torch задаётся один раз на процесс
        res = run(p, pcm, a.repeat)
        rows.append({"threads": n, "interop": p.threads.get("interop"), **res})
    print(f"\n{'ASR_CPU_THREADS':>16} {'interop':>8} {'аудио, с':>9} {'средн., мс':>11} {'p95, мс':>9} {'RTF':>7}")
    for r in rows:
        print(f"{r['threads']:>16} {r['interop']:>8} {r['audio_s']:>9} {r['avg_ms']:>11} {r['p95_ms']:>9} {r['rtf']:>7}")
    best = min(rows, key=lambda r: r["avg_ms"])
    print(f"\nЛучшая задержка: ASR_CPU_THREADS={best['threads']} (RTF {best['rtf']}). "
          "Если разница между вариантами мала, берите МЕНЬШЕЕ число потоков: на shared-host это оставляет CPU соседним сервисам.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
