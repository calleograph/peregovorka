"""Админка → «Трансляции»: состояние подсистемы (идущие эфиры, зрители, место на томе HLS) и проверки по цепочке зависимостей (services/media_checks.py).
Настройки — общая форма группы `live`; включение и перезапуск службы Egress — /admin/components."""
from __future__ import annotations

import os
import shutil
from ..services import server_components as _sc
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_admin
from ..models import Broadcast, LiveDestination, Recording, Room
from ..services import media_checks
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
        await st.video.api.probe_livekit()
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
    load = await st.video.api.cpu_load(cfg.egress_health_url) if cfg.egress_health_url else None
    dest_rows = (await db.execute(select(LiveDestination.status, func.count()).group_by(LiveDestination.status))).all()
    dests = {k: int(v) for k, v in dest_rows}
    rec_rows = (await db.execute(select(Recording.room_id, Recording.status).where(Recording.kind == "mix_video", Recording.status.in_(("processing", "finishing"))))).all()
    installed = _sc.enabled_on_server(st.settings, _sc.channel_for(st.settings), "video")
    hls_bytes = _dir_size(base / "b") if (base / "b").is_dir() else 0
    items = await media_checks.checks(st, db, "live")
    warnings = media_checks.warnings_of(items) + [w for w in (
        f"Площадок с ошибкой: {dests.get('failed', 0)} (подробности — в настройках комнат)." if dests.get("failed") and active else None,
        f"Зрителей сейчас {total_viewers}: с основного сервера раздаётся по потоку каждому. Для больших аудиторий подключите CDN." if total_viewers >= cfg.direct_viewers_warn else None,
    ) if w]
    return {"enabled": bool(cfg.enabled), "service_installed": installed, "livekit_ok": livekit_ok, "livekit_error": livekit_error, "running": len(active), "max_concurrent": cfg.max_concurrent,
            "viewers": total_viewers, "active": active, "egress_cpu_load": load, "max_cpu_load": cfg.max_cpu_load, "destinations": dests, "recordings_running": len(rec_rows),
            "cdn": {"enabled": bool(cfg.cdn_enabled), "base_url": cfg.cdn_base_url}, "hls_bytes": hls_bytes, "free_bytes": free, "secret_ok": secret_ok,
            "failed": [{"room": n, "error": b.error, "at": b.ended_at or b.started_at} for b, n in failed], "warnings": warnings, "checks": items, "summary": media_checks.summary_of(items),
            "now": datetime.now(timezone.utc)}


@router.post("/cdn/check")
async def cdn_check(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """«Проверить CDN»: https-адрес доставки разрешается в публичные адреса (внутренние не допускаются: сервер не должен ходить по внутренней сети по чужому адресу), затем запрашивается
    `/hls/healthz` — его CDN должен переслать с источника. Редиректы не выполняются."""
    import time  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from ..services.live_destinations import DestinationError, check_host_allowed  # noqa: PLC0415

    cfg = await request.app.state.settings_svc.get(db, "live")
    base = cfg.cdn_base_url
    if not base:
        return {"ok": False, "message": "Сначала задайте и сохраните «Адрес доставки CDN»."}
    host = base.split("://", 1)[1].split(":")[0].split("/")[0]
    try:
        await check_host_allowed(host, allow_private=False)
    except DestinationError as exc:
        return {"ok": False, "message": exc.message}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as c:
            r = await c.get(f"{base}/hls/healthz")
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"CDN не отвечает: {type(exc).__name__}."}
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code != 200 or r.text.strip() != "ok":
        return {"ok": False, "ms": ms, "message": f"CDN ответил {r.status_code}, а не «ok» от источника: проверьте, что он пересылает /hls/ на ваш сервер без изменений."}
    return {"ok": True, "ms": ms, "cache": r.headers.get("age") or r.headers.get("x-cache") or "", "message": f"CDN отвечает и пересылает запросы на источник ({ms} мс)."}
