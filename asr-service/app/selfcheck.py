"""Самопроверка образа ASR: `python -m app.selfcheck`. Выполняется при СБОРКЕ образа (Dockerfile), в CI и при запуске службы.
Проверяет то, что не видно по «модель загружена»: согласованность установленных пакетов (`pip check`) и реальную инициализацию Silero VAD (ONNX) с одним прогоном."""
from __future__ import annotations

import json
import subprocess
import sys

import numpy as np


def pip_problems() -> list[str]:
    r = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
    return [ln.strip() for ln in (r.stdout + r.stderr).splitlines() if ln.strip()] if r.returncode != 0 else []


def vad_problem() -> str | None:
    """None — VAD создаётся и отвечает; иначе причина (например, ModuleNotFoundError: onnxruntime)."""
    try:
        from .audio.vad import SileroVad  # noqa: PLC0415

        p = SileroVad().speech_prob(np.zeros(512, dtype=np.int16))
        return None if 0.0 <= p <= 1.0 else f"неверный ответ VAD: {p}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:200]}"


def main() -> int:
    pip, vad = pip_problems(), vad_problem()
    print(json.dumps({"pip_check": pip or "ok", "vad": vad or "ok"}, ensure_ascii=False))
    return 1 if (pip or vad) else 0


if __name__ == "__main__":
    sys.exit(main())
