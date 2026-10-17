"""Время всего задания «протокол/резюме», а не одного запроса к модели.

Цель — уложиться в `TARGET_S` (конвейер подбирает размер ответов под достигнутую скорость модели и пропускает необязательные шаги);
жёсткий предел `MAX_S` ставит планировщик (`asyncio.timeout` вокруг всего задания: идущий запрос к модели отменяется, новые не начинаются).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

TARGET_S = 300.0     # обычный документ — за 5 минут и быстрее
MAX_S = 600.0        # жёсткий предел с момента, как задание взято в работу
TAIL_RESERVE_S = 45.0    # столько времени оставляем на сборку документа после последнего прохода по стенограмме
SUMMARY_MIN_S = 60.0     # итоговый абзац от модели просим, только если до цели осталось не меньше


class JobTimeout(Exception):
    """Время задания вышло или его не хватит, чтобы довести документ до конца. `progress` — что успели (для сообщения пользователю)."""

    def __init__(self, message: str, progress: dict | None = None):
        super().__init__(message)
        self.progress = progress or {}


@dataclass
class Budget:
    target_s: float = TARGET_S
    max_s: float = MAX_S
    t0: float = field(default_factory=time.monotonic)
    progress: dict = field(default_factory=dict)      # для интерфейса: этап (prepare | analysis | assemble) и обработанные фрагменты

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def remaining(self) -> float:
        """До жёсткого предела."""
        return self.max_s - self.elapsed()

    def soft_left(self) -> float:
        """До цели (может быть отрицательным: цель пропущена, но предел ещё не наступил)."""
        return self.target_s - self.elapsed()


def pass_cap(budget: Budget, tokens_per_s: float | None, passes_left: int, *, default: int = 450, lo: int = 120, hi: int = 600) -> int:
    """Потолок ответа очередного прохода: оставшееся до цели время (за вычетом хвоста на сборку), поделённое между оставшимися проходами,
    на измеренной скорости модели. Пока скорость неизвестна — `default`. Нижняя граница не даёт ответу стать бесполезно коротким."""
    if not tokens_per_s or tokens_per_s <= 0:
        return default
    secs = max(budget.soft_left() - TAIL_RESERVE_S, 0.0)
    return int(max(lo, min(hi, secs * tokens_per_s * 0.85 / max(passes_left, 1))))
