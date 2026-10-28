"""Админка → «Трансляции»: состояние подсистемы (идущие эфиры, зрители, место на томе HLS, связь с LiveKit, секрет подписи). Только чтение; настройки — общая форма группы `live`.
Нагрузка контейнера Egress (процессор, память, сеть) из приложения не видна: её показывает `docker stats` и `scripts/video.sh status`."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_admin
from ..models import Broadcast, Room
from ..services.live import LIVE_ACTIVE

router = APIRouter(prefix="/admin/live", tags=["admin"])


def _dir_size(path, cap_s: float = 2.0) -> int:
    """Сколько занимает каталог HLS (обход ограничен по времени: на большом томе не задерживает ответ)."""
    import time  # noqa: PLC0415

    total, t0 = 0, time.monotonic()
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:
                pass
        if time.monotonic() - t0 > cap_s:
            break
    return total


@router.get("/status")
async def live_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    st = request.app.state
    cfg = await st.settings_svc.get(db, "live")
    live = st.live
    base = live.live_dir()
    try:
        free = shutil.disk_usage(base if base.exists() else base.parent).free
    except OSError:
        free = None
    livekit_ok, livekit_error = True, None
    try:
        await st.video.api.probe()
    except Exception as exc:  # noqa: BLE001
        livekit_ok, livekit_error = False, type(exc).__name__
    rows = list((await db.execute(select(Broadcast, Room.name).join(Room, Room.id == Broadcast.room_id).where(Broadcast.status.in_(LIVE_ACTIVE)).order_by(Broadcast.started_at))).all())
    active, total_viewers = [], 0
    for b, name in rows:
        v = await live.viewers(b.room_id)
        total_viewers += v
        active.append({"room": name, "status": b.status, "since": b.started_at, "quality": b.quality, "viewers": v, "hls": bool(b.hls_dir), "hls_ready": live.hls_ready(b), "by": b.started_by})
    failed = (await db.execute(select(Broadcast, Room.name).join(Room, Room.id == Broadcast.room_id).where(Broadcast.status == "failed").order_by(Broadcast.started_at.desc()).limit(5))).all()
    secret_ok = (base / "nginx" / "secret.conf").is_file()
    installed = str(st.settings.video_enabled).strip().lower() in ("yes", "true", "1", "on")
    hls_bytes = _dir_size(base / "b") if (base / "b").is_dir() else 0
    warnings = [w for w in (
        "Служба Egress не установлена (VIDEO_ENABLED=no): эфир не начнётся. Установка: sudo scripts/video.sh enable." if cfg.enabled and not installed else None,
        "Нет связи с сервером звонков: трансляции недоступны." if not livekit_ok else None,
        f"Свободного места на томе трансляций меньше {cfg.min_free_gb} ГБ: новые эфиры не начнутся." if free is not None and free < cfg.min_free_gb * 1024**3 else None,
        "Секрет подписи HLS не записан на том трансляций (нет файла nginx/secret.conf): зрители не смогут открыть поток. Проверьте права каталога данных и перезапустите web." if cfg.enabled and not secret_ok else None,
        f"Зрителей сейчас {total_viewers}: с основного сервера раздаётся по потоку каждому. Для больших аудиторий подключите CDN." if total_viewers >= cfg.direct_viewers_warn else None,
    ) if w]
    return {"enabled": bool(cfg.enabled), "service_installed": installed, "livekit_ok": livekit_ok, "livekit_error": livekit_error, "running": len(active), "max_concurrent": cfg.max_concurrent,
            "viewers": total_viewers, "active": active, "hls_bytes": hls_bytes, "free_bytes": free, "secret_ok": secret_ok,
            "failed": [{"room": n, "error": b.error, "at": b.ended_at or b.started_at} for b, n in failed], "warnings": warnings, "now": datetime.now(timezone.utc)}
