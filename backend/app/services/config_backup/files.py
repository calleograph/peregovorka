"""Файлы, которые меняются вместе с базой при импорте (изображения оформления, собранный набор сертификатов): подготовка → подмена с резервными копиями → откат при сбое.

Порядок в API: все файлы готовятся рядом с боевыми (временные имена) ДО фиксации базы; затем подменяются (старые сохраняются); затем фиксируется база. Сбой на любом шаге возвращает
прежние файлы и откатывает базу — состояние «база старая, файлы новые» невозможно, кроме момента аварии между подменой и фиксацией (набор сертификатов — производный файл и пересобирается из
базы при запуске приложения; изображение — косметика)."""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("app.config_backup")
BAK = ".import-bak"


@dataclass
class Staged:
    final: Path
    tmp: Path | None = None            # подготовленное содержимое; None + remove=True — файл нужно убрать
    remove: bool = False
    label: str = ""


@dataclass
class Promotion:
    done: list[tuple[Path, Path | None]] = field(default_factory=list)       # (боевой файл, его резервная копия или None, если раньше не было)


def tmp_name(final: Path) -> Path:
    return final.with_name(f"{final.name}.import-{uuid.uuid4().hex[:8]}.tmp")


def discard(staged: list[Staged]) -> None:
    for s in staged:
        if s.tmp is not None:
            try:
                s.tmp.unlink(missing_ok=True)
            except OSError:
                pass


def promote(staged: list[Staged]) -> Promotion:
    promo = Promotion()
    try:
        for s in staged:
            backup: Path | None = None
            if s.final.exists():
                backup = s.final.with_name(s.final.name + BAK)
                os.replace(s.final, backup)
            promo.done.append((s.final, backup))
            if not s.remove and s.tmp is not None:
                os.replace(s.tmp, s.final)
    except Exception:
        undo(promo)
        discard(staged)
        raise
    return promo


def undo(promo: Promotion) -> None:
    """Вернуть прежние файлы (в обратном порядке)."""
    for final, backup in reversed(promo.done):
        try:
            final.unlink(missing_ok=True)
            if backup is not None and backup.exists():
                os.replace(backup, final)
        except OSError:
            log.exception("Не удалось вернуть файл после неудачного импорта", extra={"file": final.name})
    promo.done.clear()


def finish(promo: Promotion) -> None:
    for _, backup in promo.done:
        if backup is not None:
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
    promo.done.clear()
