"""Подписки на события публичного API (webhooks): очередь доставок в БД, подпись HMAC, повторы с нарастающей задержкой, статусы получателя.

Гарантии и правила
* **Доставка «как минимум один раз»**: событие записывается в БД (outbox) в момент возникновения и отправляется фоновым воркером; сбой получателя или перезапуск сервиса его не теряют.
  Получатель обязан быть идемпотентным: у повторов тот же `X-Peregovorka-Event-Id`.
* **Подпись** `X-Peregovorka-Signature: v1=<hex>` = HMAC-SHA256(секрет, "<timestamp>.<тело>"). Метка времени (`X-Peregovorka-Timestamp`) входит в подпись: получатель отвергает старше 5 минут
  (защита от повторного предъявления перехваченного запроса) и помнит уже обработанные `Event-Id`. При смене секрета в окне перехода заголовок содержит две подписи.
* **Повторы**: экспоненциальная задержка 10 с · 2ⁿ (до часа) с разбросом; после `webhook_max_attempts` — «не доставлено» (можно отправить вручную).
* **Получатель** переходит в «деградацию» после серии неудач и **отключается** после устойчивой серии (или ответа 410 Gone); включается вручную. Отключённому события не отправляются.
* **SSRF**: адрес проверяется при сохранении и при КАЖДОЙ отправке (ssrf.py); переходы по редиректам не выполняются; системные прокси игнорируются.
* В событиях нет персональных данных и текстов — только идентификаторы; содержимое получатель запрашивает через API ключом.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import random
import secrets as pysecrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import WebhookDelivery, WebhookEndpoint, utcnow
from ..services.audit import write_audit
from .ssrf import UrlRejected, pinned_url, validate_url

log = logging.getLogger("app")

EVENTS: dict[str, str] = {
    "meeting.started": "Встреча началась",
    "meeting.ended": "Встреча завершена",
    "document.ready": "Протокол, резюме или карта готовы",
    "document.failed": "Не удалось сформировать протокол, резюме или карту",
    "job.completed": "Фоновая задача API завершена",
    "job.failed": "Фоновая задача API завершилась ошибкой",
    "webhook.test": "Проверочное событие (отправляется вручную)",
}
TOLERANCE_S = 300
LEASE_S = 90                      # столько времени доставка «занята» воркером; при сбое процесса затем возвращается в очередь
KEEP_ATTEMPTS = 10


# ------------------------------------------------------------------------------------------------ подпись
def new_secret() -> str:
    return "whsec_" + pysecrets.token_urlsafe(32)


def sign(secret: str, ts: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def signature_header(secrets: list[str], ts: int, body: bytes) -> str:
    return ",".join(f"v1={sign(s, ts, body)}" for s in secrets)


def verify(header: str, secrets: list[str], ts: int, body: bytes, *, now: float | None = None, tolerance: int = TOLERANCE_S) -> bool:
    """Эталонная проверка на стороне получателя (используется в тестах и приведена в документации)."""
    if abs((time.time() if now is None else now) - ts) > tolerance:
        return False
    given = [p.strip()[3:] for p in (header or "").split(",") if p.strip().startswith("v1=")]
    return any(hmac.compare_digest(g, sign(s, ts, body)) for g in given for s in secrets)


def backoff_s(attempt: int, rnd: random.Random | None = None) -> float:
    """Пауза перед следующей попыткой после `attempt`-й неудачной: 10, 20, 40 … до 3600 с, разброс ±20 %."""
    base = min(3600.0, 10.0 * 2 ** max(0, attempt - 1))
    return base * (0.8 + 0.4 * (rnd or random).random())


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


class WebhookError(Exception):
    pass


class WebhookService:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession], settings_svc, journal=None, *, transports: dict | None = None,
                 resolver=None, ca_file: str | None = None, clock=utcnow):
        from .ssrf import default_resolver  # noqa: PLC0415

        self._sm, self._svc, self.journal = session_maker, settings_svc, journal
        self._transport = (transports or {}).get("webhook")
        self._resolver = resolver or default_resolver
        self._ca, self.clock = ca_file, clock
        self._wake = asyncio.Event()
        self._sem = asyncio.Semaphore(4)
        self._tasks: set[asyncio.Task] = set()
        self._runner: asyncio.Task | None = None
        self._last_prune = 0.0

    # ------------------------------------------------------------------ секреты
    def _box(self):
        box = self._svc.box
        if box is None:
            raise WebhookError("Не задан APP_MASTER_KEY: секреты подписи хранить нечем")
        return box

    def encrypt(self, endpoint_id: uuid.UUID, secret: str) -> str:
        return self._box().encrypt(secret, aad=f"webhook:{endpoint_id}".encode())

    def secrets_of(self, ep: WebhookEndpoint, now: datetime) -> list[str]:
        out: list[str] = []
        box = self._box()
        if ep.secret_enc:
            out.append(box.decrypt(ep.secret_enc, aad=f"webhook:{ep.id}".encode()))
        if ep.previous_secret_enc and ep.previous_until and _aware(ep.previous_until) > _aware(now):
            out.append(box.decrypt(ep.previous_secret_enc, aad=f"webhook:{ep.id}".encode()))
        return out

    # ------------------------------------------------------------------ события
    async def emit(self, event_type: str, data: dict, *, room_id: uuid.UUID | str | None = None) -> int:
        """Ставит событие в очередь всем подходящим получателям. Возвращает число доставок. Не бросает исключений наружу (см. emit_safe)."""
        async with self._sm() as db:
            cfg = await self._svc.get(db, "api")
            if not cfg.enabled:                                    # type: ignore[attr-defined]
                return 0
            eps = (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.enabled.is_(True), WebhookEndpoint.status != "disabled"))).scalars().all()
            now = self.clock()
            event_id = "evt_" + uuid.uuid4().hex[:24]
            envelope = {"id": event_id, "type": event_type, "api_version": "1", "created": now.isoformat(), "data": data}
            n = 0
            for ep in eps:
                if ep.events and event_type not in ep.events:
                    continue
                if ep.rooms is not None and (room_id is None or str(room_id) not in ep.rooms):
                    continue
                db.add(WebhookDelivery(endpoint_id=ep.id, event_id=event_id, event_type=event_type, payload=envelope, status="pending", next_attempt_at=now))
                n += 1
            if n:
                await db.commit()
        if n:
            self._wake.set()
        return n

    def emit_safe(self, event_type: str, data: dict, *, room_id=None) -> None:
        """Из обработчиков событий приложения: фоновая постановка в очередь; любая ошибка — только в журнал."""
        async def go() -> None:
            try:
                await self.emit(event_type, data, room_id=room_id)
            except Exception:  # noqa: BLE001
                log.exception("Не удалось поставить событие webhook в очередь", extra={"event": event_type})
        try:
            t = asyncio.get_running_loop().create_task(go())
        except RuntimeError:
            return
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ отправка
    async def deliver_due(self, now: datetime | None = None, limit: int = 20) -> int:
        """Берёт готовые к отправке доставки (занимая их на время попытки) и отправляет параллельно. Возвращает число попыток."""
        now = now or self.clock()
        async with self._sm() as db:
            rows = (await db.execute(select(WebhookDelivery.id).join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
                                     .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now,
                                            WebhookEndpoint.enabled.is_(True), WebhookEndpoint.status != "disabled")
                                     .order_by(WebhookDelivery.next_attempt_at).limit(limit))).scalars().all()
            claimed: list[uuid.UUID] = []
            for did in rows:                                        # атомарно «занять»: второй воркер/процесс ту же доставку не возьмёт
                res = await db.execute(update(WebhookDelivery).where(WebhookDelivery.id == did, WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now)
                                       .values(next_attempt_at=now + timedelta(seconds=LEASE_S)))
                if res.rowcount == 1:
                    claimed.append(did)
            await db.commit()

        async def one(did: uuid.UUID) -> None:
            async with self._sem:
                try:
                    await self.attempt(did, now=now)
                except Exception:  # noqa: BLE001 — сбой одной доставки не должен останавливать остальные и воркер
                    log.exception("Сбой попытки доставки webhook", extra={"delivery": str(did)})

        await asyncio.gather(*(one(d) for d in claimed))
        return len(claimed)

    async def attempt(self, delivery_id: uuid.UUID, *, now: datetime | None = None) -> WebhookDelivery | None:
        now = now or self.clock()
        async with self._sm() as db:
            d = await db.get(WebhookDelivery, delivery_id)
            ep = await db.get(WebhookEndpoint, d.endpoint_id) if d else None
            if d is None or ep is None:
                return None
            cfg = await self._svc.get(db, "api")
            err, status, ms = None, None, 0
            body = json.dumps(d.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            t0 = time.perf_counter()
            try:
                host, port, scheme, ips = await validate_url(ep.url, allow_http=cfg.webhook_allow_http, allow_text=cfg.webhook_allow_hosts, resolver=self._resolver)  # type: ignore[attr-defined]
                secrets = self.secrets_of(ep, now)
                if not secrets:
                    raise WebhookError("у получателя нет секрета подписи")
                ts = int(now.timestamp())
                default_port = 443 if scheme == "https" else 80
                headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "Peregovorka-Webhook/1", "Host": host if port == default_port else f"{host}:{port}",
                           "X-Peregovorka-Event": d.event_type, "X-Peregovorka-Event-Id": d.event_id, "X-Peregovorka-Delivery": str(d.id),
                           "X-Peregovorka-Timestamp": str(ts), "X-Peregovorka-Signature": signature_header(secrets, ts, body),
                           "X-Peregovorka-Attempt": str(d.attempts + 1)}
                if d.manual_retries:
                    headers["X-Peregovorka-Retry"] = "manual"
                verify_tls: bool | str = (self._ca or True) if getattr(cfg, "webhook_use_corporate_ca", False) else True
                async with httpx.AsyncClient(timeout=httpx.Timeout(float(cfg.webhook_timeout_s), connect=min(5.0, float(cfg.webhook_timeout_s))),  # type: ignore[attr-defined]
                                             follow_redirects=False, trust_env=False, verify=verify_tls, transport=self._transport) as client:
                    async with client.stream("POST", pinned_url(ep.url, ips[0]), content=body, headers=headers, extensions={"sni_hostname": host}) as r:
                        status = r.status_code            # тело ответа не читаем: объём и содержимое чужого ответа нам не нужны
            except UrlRejected as exc:
                err = f"адрес запрещён политикой: {exc}"[:280]
            except WebhookError as exc:
                err = str(exc)[:280]
            except httpx.TimeoutException:
                err = f"таймаут {cfg.webhook_timeout_s} с"        # type: ignore[attr-defined]
            except httpx.HTTPError as exc:
                err = f"сбой соединения ({type(exc).__name__})"
            ms = int((time.perf_counter() - t0) * 1000)
            ok = status is not None and 200 <= status < 300
            if status is not None and not ok:
                err = f"HTTP {status}" + (" (редиректы не выполняются)" if 300 <= status < 400 else "")
            await self._record(db, d, ep, cfg, ok=ok, status=status, err=err, ms=ms, now=now)
            await db.commit()
            return d

    async def _record(self, db: AsyncSession, d: WebhookDelivery, ep: WebhookEndpoint, cfg, *, ok: bool, status: int | None, err: str | None, ms: int, now: datetime) -> None:
        d.attempts += 1
        d.last_status, d.last_error = status, None if ok else err
        d.attempt_log = (list(d.attempt_log or []) + [{"at": now.isoformat(), "status": status, "ms": ms, "error": None if ok else err}])[-KEEP_ATTEMPTS:]
        ep.updated_at = now
        if ok:
            d.status, d.delivered_at = "delivered", now
            ep.consecutive_failures, ep.last_success_at, ep.last_error = 0, now, None
            if ep.status == "degraded":
                ep.status = "active"
            return
        ep.consecutive_failures += 1
        ep.last_failure_at, ep.last_error = now, err
        if d.attempts >= cfg.webhook_max_attempts:
            d.status = "failed"
        else:
            d.next_attempt_at = now + timedelta(seconds=backoff_s(d.attempts))
        if status == 410:
            await self._disable(db, ep, "получатель ответил 410 Gone: события ему больше не нужны", now)
        elif ep.consecutive_failures >= cfg.webhook_disable_after:
            await self._disable(db, ep, f"{ep.consecutive_failures} неудачных попыток подряд", now)
        elif ep.consecutive_failures >= cfg.webhook_degraded_after and ep.status == "active":
            ep.status = "degraded"
            if self.journal is not None:
                self.journal.emit("api", "webhook_degraded", level="warn", message=f"Получатель «{ep.name}»: {ep.consecutive_failures} неудач подряд", data={"endpoint": str(ep.id)})

    async def _disable(self, db: AsyncSession, ep: WebhookEndpoint, reason: str, now: datetime) -> None:
        ep.status, ep.disabled_reason = "disabled", reason[:200]
        await write_audit(db, actor_user_id=None, actor_name="system", action="webhook.auto_disable", target_type="webhook", target_id=str(ep.id), details={"name": ep.name, "reason": reason})
        if self.journal is not None:
            self.journal.emit("api", "webhook_disabled", level="error", message=f"Получатель «{ep.name}» отключён: {reason}", data={"endpoint": str(ep.id)})

    # ------------------------------------------------------------------ ручные действия
    async def retry(self, db: AsyncSession, delivery_id: uuid.UUID) -> WebhookDelivery | None:
        d = await db.get(WebhookDelivery, delivery_id)
        if d is None:
            return None
        d.status, d.next_attempt_at, d.manual_retries = "pending", self.clock(), d.manual_retries + 1
        await db.flush()
        return d

    async def send_test(self, endpoint_id: uuid.UUID) -> dict:
        """Проверочное событие `webhook.test` ТОЛЬКО этому получателю, отправка сразу; возвращает итог попытки."""
        async with self._sm() as db:
            ep = await db.get(WebhookEndpoint, endpoint_id)
            if ep is None:
                raise WebhookError("Получатель не найден")
            now = self.clock()
            eid = "evt_" + uuid.uuid4().hex[:24]
            d = WebhookDelivery(endpoint_id=ep.id, event_id=eid, event_type="webhook.test", status="pending", next_attempt_at=now + timedelta(seconds=LEASE_S),
                                payload={"id": eid, "type": "webhook.test", "api_version": "1", "created": now.isoformat(), "data": {"message": "Проверочное событие"}})
            db.add(d)
            await db.commit()
            did = d.id
        res = await self.attempt(did, now=now)
        async with self._sm() as db:
            d2 = await db.get(WebhookDelivery, did)
            if d2 is not None and d2.status == "pending":          # проверочное событие не повторяется само
                d2.status = "failed"
                await db.commit()
        return {"ok": bool(res and res.status == "delivered"), "status": res.last_status if res else None, "error": res.last_error if res else "не отправлено", "delivery_id": str(did)}

    # ------------------------------------------------------------------ воркер
    async def prune(self) -> int:
        async with self._sm() as db:
            cfg = await self._svc.get(db, "api")
            res = await db.execute(delete(WebhookDelivery).where(WebhookDelivery.status != "pending", WebhookDelivery.created_at < self.clock() - timedelta(days=cfg.webhook_retention_days)))  # type: ignore[attr-defined]
            await db.commit()
            return res.rowcount or 0

    async def _run(self) -> None:
        while True:
            try:
                n = await self.deliver_due()
                if time.monotonic() - self._last_prune > 3600:
                    self._last_prune = time.monotonic()
                    await self.prune()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Сбой воркера webhook")
                n = 0
            if n == 0:
                self._wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=5.0)

    def start(self) -> None:
        self._last_prune = time.monotonic()
        self._runner = asyncio.get_running_loop().create_task(self._run(), name="webhook-delivery")

    async def stop(self) -> None:
        if self._runner:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(self._runner), 3)
        await self.drain()
