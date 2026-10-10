"""Проверки после импорта: что реально заработало на новом сервере. Каждая проверка ограничена по времени; результат — одно из трёх: restored (работает), needs_attention (импортировано, но
не отвечает или требует действия администратора), failed (проверку выполнить не удалось). Используются существующие проверки разделов («Проверить» у каталога, почты, хранилища)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from ...models import ApiProfile, LdapProfile, MailProfile, SipProfile, StorageProfile
from ..settings import SettingsError

log = logging.getLogger("app.config_backup")
TIMEOUT_S = 30


async def _one(component: str, item: str, fn: Callable[[], Awaitable[tuple[bool, str]]]) -> dict[str, Any]:
    try:
        ok, msg = await asyncio.wait_for(fn(), TIMEOUT_S)
        return {"component": component, "item": item, "status": "restored" if ok else "needs_attention", "message": msg}
    except asyncio.TimeoutError:
        return {"component": component, "item": item, "status": "needs_attention", "message": f"нет ответа за {TIMEOUT_S} с — проверьте доступность и адрес"}
    except Exception as exc:  # noqa: BLE001
        log.warning("Проверка после импорта не выполнена: %s %s", component, type(exc).__name__)
        return {"component": component, "item": item, "status": "failed", "message": f"проверку выполнить не удалось ({type(exc).__name__})"}


async def run_checks(app, db) -> list[dict[str, Any]]:
    st = app.state
    out: list[dict[str, Any]] = []
    try:
        await st.ca.rebuild(db)
    except Exception:  # noqa: BLE001
        out.append({"component": "Сертификаты УЦ", "item": "набор доверенных сертификатов", "status": "failed", "message": "не удалось пересобрать набор сертификатов"})
    # --- каталог
    for r in (await db.execute(select(LdapProfile).where(LdapProfile.enabled.is_(True)).order_by(LdapProfile.position))).scalars().all():
        async def ldap(r=r):
            res = await asyncio.to_thread(st.ldap.diagnose, r)
            bad = next((s for s in res.get("stages", []) if not s.get("ok")), None)
            return bool(res.get("ok")), "подключение к каталогу работает" if res.get("ok") else f"этап «{bad.get('stage')}»: {bad.get('message', 'ошибка')}" if bad else "ошибка"
        out.append(await _one("Каталог (LDAP/AD)", r.name, ldap))
    # --- почта
    m = (await db.execute(select(MailProfile).where(MailProfile.is_active.is_(True)))).scalars().first()
    if m is not None:
        async def mail(m=m):
            from ..mail import check_connection  # noqa: PLC0415

            res = await asyncio.to_thread(check_connection, st.mail.config(m))
            return bool(res.get("ok")), "соединение с почтовым сервером установлено" if res.get("ok") else f"этап «{res.get('stage', '—')}»: {res.get('message', 'ошибка')}"
        out.append(await _one("Почта (SMTP)", m.name, mail))
    # --- хранилища
    for s in (await db.execute(select(StorageProfile))).scalars().all():
        async def storage(s=s):
            try:
                msg = await st.files.test_profile(db, str(s.id))
                return True, msg
            except (SettingsError, Exception) as exc:  # noqa: BLE001
                return False, str(exc)[:200]
        out.append(await _one("Файловые хранилища", s.name, storage))
    # --- профили внешних API
    for p in (await db.execute(select(ApiProfile))).scalars().all():
        async def api(p=p):
            tr = getattr(st, "test_transports", {}) or {}
            ca = st.settings.ldap_ca_file or None
            res = await st.profiles.get_settings(db, p.kind, str(p.id))
            if p.kind == "anonymizer":
                from ...integrations.anonymizer import AnonymizerClient  # noqa: PLC0415

                ok, msg, _ = await AnonymizerClient(res.settings, ca_file=ca, transport=tr.get("anonymizer")).test()  # type: ignore[arg-type]
            else:
                ok, msg, _ = await st.local_llm.client(res.settings, ca_file=ca, transport=tr.get("llm")).test()  # type: ignore[arg-type]
            return ok, msg or ("API отвечает" if ok else "нет ответа")
        out.append(await _one(f"Внешний API ({'обезличивание' if p.kind == 'anonymizer' else 'языковая модель'})", p.name, api))
    # --- SIP: идентификаторы транков старого LiveKit не переносятся
    for s in (await db.execute(select(SipProfile))).scalars().all():
        out.append({"component": "SIP-телефония", "item": s.name, "status": "needs_attention", "message": "подключение перенесено, но транки LiveKit нового сервера ещё не созданы — выполните синхронизацию в разделе «SIP-телефония»"})
    return out


def summarize(report: list[dict[str, Any]]) -> dict[str, int]:
    return {k: sum(1 for r in report if r["status"] == k) for k in ("restored", "needs_attention", "failed")}
