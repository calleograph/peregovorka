#!/usr/bin/env python3
"""Сквозная проверка транскрибации (регрессия 0.24.1): LiveKit → подписка воркера → Silero VAD → GigaAM → текст в стенограмме.
Запуск: python3 tests/live/asr_e2e.py <образ ASR> <каталог с весами GigaAM (v3_e2e_rnnt.ckpt)>. Нужны Docker и сеть для образа LiveKit; код возврата 0 — текст получен.
Образ без onnxruntime (или любая другая поломка VAD) даёт код 1 и причину в выводе."""
import subprocess, sys, time
from pathlib import Path

image, models = sys.argv[1], str(Path(sys.argv[2]).resolve())
repo = Path(__file__).resolve().parents[2]
tag = next(l.split("=", 1)[1].strip() for l in (repo / ".env.example").read_text(encoding="utf-8").splitlines() if l.startswith("LIVEKIT_IMAGE_TAG="))
lk = subprocess.check_output(["docker", "run", "-d", "--rm", "--network", "host", "-e", "LIVEKIT_KEYS=devkey: " + "s" * 40, f"livekit/livekit-server:{tag}", "--bind", "127.0.0.1"], text=True).strip()
try:
    time.sleep(4)
    r = subprocess.run(["docker", "run", "--rm", "--network", "host", "-v", f"{models}:/models:ro", "-v", f"{repo / 'tests/live/asr_e2e_inner.py'}:/e2e.py:ro", image, "python", "/e2e.py"])
    sys.exit(r.returncode)
finally:
    subprocess.run(["docker", "rm", "-f", lk], capture_output=True)
