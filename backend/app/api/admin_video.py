"""Админка → «Видеозапись»: состояние функции (сколько записей идёт и занято, свободное место) и проверки по цепочке зависимостей (services/media_checks.py).
Состояние и управление службой Egress — /admin/components (помощник на хосте видит контейнер, backend — нет)."""
from __future__ import annotations

import shutil
from ..services import server_components as _sc
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_admin
from ..models import Recording
from ..services import media_checks

router = APIRouter(prefix="/admin/video", tags=["admin"])


@router.get("/status")
async def video_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    st = request.app.state
    cfg = await st.settings_svc.get(db, "video")
    rows = (await db.execute(select(Recording.status, func.count(), func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.kind == "mix_video").group_by(Recording.status))).all()
    by = {s: (int(n), int(b)) for s, n, b in rows}
    root = Path(st.settings.recordings_path)
    try:
        free = shutil.disk_usage(root if root.exists() else root.parent).free
    except OSError:
        free = None
    livekit_ok, livekit_error = True, None
    try:
        await st.video.api.probe_livekit()
    except Exception as exc:  # noqa: BLE001
        livekit_ok, livekit_error = False, type(exc).__name__
    items = await media_checks.checks(st, db, "video")
    running = by.get("processing", (0, 0))[0] + by.get("finishing", (0, 0))[0]
    return {
        "enabled": bool(cfg.enabled), "service_installed": _sc.enabled_on_server(st.settings, _sc.channel_for(st.settings), "video"), "livekit_ok": livekit_ok, "livekit_error": livekit_error,
        "running": running, "max_concurrent": cfg.max_concurrent,
        "files": {"ready": by.get("ready", (0, 0))[0], "failed": by.get("failed", (0, 0))[0], "bytes": sum(b for _, b in by.values())},
        "quota_gb": cfg.quota_gb, "min_free_gb": cfg.min_free_gb, "free_bytes": free,
        "warnings": media_checks.warnings_of(items) + [w for w in (
            f"Объём видеозаписей достиг предела {cfg.quota_gb} ГБ: новые записи не начнутся." if cfg.quota_gb and sum(b for _, b in by.values()) >= cfg.quota_gb * 1024**3 else None,
        ) if w],
        "checks": items, "summary": media_checks.summary_of(items),
    }
