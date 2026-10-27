"""Центральная защита входа от перебора.

Несколько НЕЗАВИСИМЫХ серверных счётчиков (скользящее окно в Redis; не зависят от cookie, браузера, режима инкогнито, User-Agent, перезапуска и числа экземпляров backend):
  * по нормализованной учётной записи (`alice`, `ALICE`, `EXAMPLE\\alice` и `alice@example.org` — одна запись);
  * по IP-адресу (IPv6 — по /64);
  * по паре «запись + адрес»;
  * по общей интенсивности запросов к входу (на адрес и на весь сервер);
  * по числу РАЗНЫХ записей, на которые не удалось войти с одного адреса (подбор по списку).

Реакции нарастают: обычная ошибка → CAPTCHA (по порогу любого счётчика) → замедление ответа → «защита каталога» (попытки с неизвестных адресов не доходят до AD) →
временная блокировка адреса (длительность растёт при повторах, но ограничена). Учётная запись целиком НЕ блокируется: иначе любой мог бы заблокировать чужую запись несколькими запросами.
Успешный вход сбрасывает счётчики записи и пары, но не адреса — нельзя «обнулить» перебор, имея одну верную запись. Общие адреса (NAT, прокси: с адреса вошли несколько разных
пользователей, либо адрес указан администратором) жёстко не блокируются — для них остаются CAPTCHA и замедление.

Отказ хранилища (Redis) — не «пропустить всех»: вход закрывается с понятным сообщением (`GuardUnavailable`); уже идущие конференции вход не затрагивает.
Ответы не раскрывают причину и не отличают существующую запись от несуществующей: счётчики считаются по введённой строке одинаково.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import secrets
import time
from dataclasses import dataclass

from .ipres import in_nets, ip_key, parse_nets

log = logging.getLogger("app.security.loginguard")

KNOWN_KEEP_S = 30 * 86400
SHARED_KEEP_S = 24 * 3600


class GuardUnavailable(Exception):
    """Хранилище счётчиков недоступно: вход закрыт (fail closed), но не навсегда."""


@dataclass
class Verdict:
    allow: bool = True
    retry_after: int = 0
    rule: str = ""                   # внутреннее название сработавшего правила (в ответ пользователю не попадает; в журнал безопасности — да)
    captcha: bool = False
    captcha_reason: str = ""         # ip | account | pair | flood | always
    delay_s: float = 0.0


def account_key(login: str) -> str:
    """Нормализованный идентификатор записи: без домена (`DOMAIN\\user`), без суффикса `@domain`, без регистра и пробелов по краям."""
    s = (login or "").strip().lower()
    if "\\" in s:
        s = s.split("\\", 1)[1]
    return s.partition("@")[0][:128]


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


class LoginGuard:
    def __init__(self, redis):
        self.r = redis
        self._svc = None
        self._sm = None
        self.events = None          # security.events.SecurityEvents (журнал безопасности); задаётся при запуске

    def bind(self, settings_svc, session_maker) -> None:
        self._svc, self._sm = settings_svc, session_maker

    # --------------------------------------------------------------------------------------- настройки
    async def cfg(self):
        async with self._sm() as db:
            return await self._svc.get(db, "login_protection"), await self._svc.get(db, "captcha")

    # --------------------------------------------------------------------------------------- счётчики
    async def _add(self, key: str, now: float, keep_s: float, member: str | None = None) -> None:
        p = self.r.pipeline()
        p.zadd(key, {member or f"{now:.6f}:{secrets.token_hex(3)}": now})
        p.zremrangebyscore(key, 0, now - keep_s)
        p.expire(key, int(keep_s) + 60)
        await p.execute()

    async def _count(self, key: str, now: float, window_s: float) -> int:
        return int(await self.r.zcount(key, now - window_s, "+inf"))

    async def _oldest(self, key: str, now: float, window_s: float) -> float | None:
        got = await self.r.zrangebyscore(key, now - window_s, "+inf", start=0, num=1, withscores=True)
        return float(got[0][1]) if got else None

    @staticmethod
    def _k(kind: str, *parts: str) -> str:
        return "sec:" + kind + ":" + ":".join(parts)

    # --------------------------------------------------------------------------------------- блокировки адресов
    async def blocked(self, ipk: str, now: float | None = None) -> dict | None:
        raw = await self.r.get(self._k("blk", ipk))
        if not raw:
            return None
        try:
            b = json.loads(raw)
        except ValueError:
            return None
        return b if float(b.get("until", 0)) > (now or time.time()) else None

    async def block_ip(self, ip: str, minutes: int, reason: str, *, rule: str, manual: bool = False, actor: str = "", failures: int = 0, level: int = 1) -> dict:
        ipk, now = ip_key(ip), time.time()
        rec = {"ip": ipk, "since": now, "until": now + minutes * 60, "reason": reason[:200], "rule": rule, "manual": manual, "actor": actor[:80], "failures": failures, "level": level}
        p = self.r.pipeline()
        p.set(self._k("blk", ipk), json.dumps(rec, ensure_ascii=False), ex=int(minutes * 60) + 5)
        p.zadd("sec:blocks", {ipk: rec["until"]})
        await p.execute()
        return rec

    async def unblock_ip(self, ip: str) -> bool:
        """Снимает ТОЛЬКО блокировку адреса; счётчики неудач и история повторов остаются (иначе снятие блокировки обнуляло бы след подозрительной активности)."""
        ipk = ip_key(ip)
        p = self.r.pipeline()
        p.delete(self._k("blk", ipk))
        p.zrem("sec:blocks", ipk)
        res = await p.execute()
        return bool(res[0])

    async def list_blocks(self) -> list[dict]:
        now = time.time()
        await self.r.zremrangebyscore("sec:blocks", 0, now)
        out = []
        for ipk in await self.r.zrange("sec:blocks", 0, 500):
            b = await self.blocked(ipk, now)
            if b:
                out.append(b)
        return sorted(out, key=lambda b: -b["since"])

    async def _escalate(self, ipk: str, days: int) -> int:
        n = await self.r.incr(self._k("esc", ipk))
        await self.r.expire(self._k("esc", ipk), days * 86400)
        return int(n)

    async def is_shared(self, ipk: str, lp, now: float) -> bool:
        """Общий адрес (NAT, прокси): указан администратором либо с него успешно вошло несколько разных пользователей."""
        if in_nets(ipk.split("/")[0], parse_nets(lp.nat_addresses)):
            return True
        return int(await self.r.zcount(self._k("shared", ipk), now - SHARED_KEEP_S, "+inf")) >= lp.shared_ip_accounts

    async def _known(self, ah: str, ipk: str, now: float) -> bool:
        return await self.r.zscore(self._k("known", ah), ipk) is not None

    # --------------------------------------------------------------------------------------- проверки до попытки
    async def check_block(self, ip: str, login: str) -> Verdict:
        """Только запрет (без подсчёта запросов и CAPTCHA): им пользуются все пути проверки пароля, в том числе второй фактор и повторное подтверждение пароля."""
        try:
            lp, _cp = await self.cfg()
            if not lp.enabled:
                return Verdict()
            now, ipk = time.time(), ip_key(ip)
            b = await self.blocked(ipk, now)
            if b:
                return Verdict(False, max(1, math.ceil(b["until"] - now)), b.get("rule", "ip_block"))
            return await self._acct_guard(lp, ipk, _h(account_key(login)), now)
        except GuardUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("Хранилище защиты входа недоступно", exc_info=True)
            raise GuardUnavailable() from exc

    async def _acct_guard(self, lp, ipk: str, ah: str, now: float) -> Verdict:
        win = lp.window_minutes * 60
        f_acct = await self._count(self._k("f", "acct", ah), now, win)
        if f_acct >= lp.account_guard_failures and not (lp.unknown_device_rule and await self._known(ah, ipk, now)):
            oldest = await self._oldest(self._k("f", "acct", ah), now, win)
            return Verdict(False, max(1, math.ceil((oldest or now) + win - now)), "account_guard")
        return Verdict()

    async def gate(self, ip: str, login: str) -> Verdict:
        """Перед попыткой входа: блокировки, лимит запросов, нужна ли CAPTCHA, на сколько замедлить ответ о неудаче. Считает ЗАПРОС (интенсивность), не неудачу."""
        try:
            lp, cp = await self.cfg()
            now, ipk, ah = time.time(), ip_key(ip), _h(account_key(login))
            always = cp.mode == "always"
            if not lp.enabled:
                return Verdict(captcha=always, captcha_reason="always" if always else "")
            b = await self.blocked(ipk, now)
            if b:
                return Verdict(False, max(1, math.ceil(b["until"] - now)), b.get("rule", "ip_block"))
            await self._add(self._k("rate", "ip", ipk), now, 60)
            await self._add(self._k("rate", "all"), now, 60)
            rate_ip = await self._count(self._k("rate", "ip", ipk), now, 60)
            if rate_ip > lp.rate_ip_per_minute and (rate_ip > lp.rate_ip_per_minute * 10 or not await self.is_shared(ipk, lp, now)):
                return Verdict(False, 30, "rate_ip")
            flood = await self._count(self._k("rate", "all"), now, 60) > lp.rate_global_per_minute
            v = await self._acct_guard(lp, ipk, ah, now)
            if not v.allow:
                return v
            cwin = cp.window_minutes * 60
            f_ip = await self._count(self._k("f", "ip", ipk), now, cwin)
            f_acct = await self._count(self._k("f", "acct", ah), now, cwin)
            f_pair = await self._count(self._k("f", "pair", ah, ipk), now, cwin)
            reason = ""
            if always:
                reason = "always"
            elif cp.mode == "after_failures":
                reason = "ip" if f_ip >= cp.failures else "account" if f_acct >= cp.failures else "pair" if f_pair >= cp.failures else ""
            if not reason and flood and cp.mode != "off":
                reason = "flood"
            ip_f_slow = await self._count(self._k("f", "ip", ipk), now, lp.window_minutes * 60)
            delay = 0.0
            if lp.slowdown_after and ip_f_slow > lp.slowdown_after:
                delay = min(lp.slowdown_max_ms, (ip_f_slow - lp.slowdown_after) * lp.slowdown_step_ms) / 1000
            return Verdict(True, 0, "", bool(reason), reason, delay)
        except Exception as exc:  # noqa: BLE001
            log.error("Хранилище защиты входа недоступно", exc_info=True)
            raise GuardUnavailable() from exc

    # --------------------------------------------------------------------------------------- итог попытки
    async def failure(self, ip: str, login: str, *, why: str = "password") -> dict | None:
        """Неудачная попытка. Возвращает запись блокировки, если именно эта попытка заблокировала адрес."""
        try:
            lp, _cp = await self.cfg()
            now, ipk, ah = time.time(), ip_key(ip), _h(account_key(login))
            keep = max(lp.window_minutes, _cp.window_minutes) * 60
            await self._add(self._k("f", "ip", ipk), now, keep)
            await self._add(self._k("f", "acct", ah), now, keep)
            await self._add(self._k("f", "pair", ah, ipk), now, keep)
            await self._add(self._k("accts", ipk), now, keep, member=ah)
            if not lp.enabled:
                return None
            win = lp.window_minutes * 60
            f_ip = await self._count(self._k("f", "ip", ipk), now, win)
            distinct = await self._count(self._k("accts", ipk), now, win)
            rule = "ip_failures" if f_ip >= lp.ip_block_failures else "spray" if distinct >= lp.spray_accounts else ""
            if not rule or await self.blocked(ipk, now):
                return None
            if await self.is_shared(ipk, lp, now):
                if self.events:
                    await self.events.emit("limit_shared", ip=ip, result="captcha_only", rule=rule, reason="общий адрес (NAT/прокси): блокировка заменена CAPTCHA и замедлением", component="auth")
                return None
            level = await self._escalate(ipk, lp.escalation_days)
            minutes = min(lp.ip_block_max_minutes, lp.ip_block_minutes * (2 ** (level - 1)))
            reason = "подбор по списку учётных записей" if rule == "spray" else "много неудачных попыток входа"
            rec = await self.block_ip(ip, minutes, reason, rule=rule, failures=f_ip, level=level)
            if self.events:
                await self.events.emit("ip_blocked", ip=ip, result="blocked", rule=rule, reason=reason, until=rec["until"], component="auth",
                                       details={"failures": f_ip, "accounts": distinct, "level": level, "minutes": minutes})
            return rec
        except Exception as exc:  # noqa: BLE001
            log.error("Хранилище защиты входа недоступно (неудача не записана)", exc_info=True)
            raise GuardUnavailable() from exc

    async def success(self, ip: str, login: str) -> None:
        try:
            now, ipk, ah = time.time(), ip_key(ip), _h(account_key(login))
            if not ip:
                await self.r.delete(self._k("f", "acct", ah))
                return
            p = self.r.pipeline()
            p.delete(self._k("f", "acct", ah), self._k("f", "pair", ah, ipk))
            p.zadd(self._k("known", ah), {ipk: now})
            p.zremrangebyscore(self._k("known", ah), 0, now - KNOWN_KEEP_S)
            p.expire(self._k("known", ah), KNOWN_KEEP_S + 3600)
            p.zadd(self._k("shared", ipk), {ah: now})
            p.zremrangebyscore(self._k("shared", ipk), 0, now - SHARED_KEEP_S)
            p.expire(self._k("shared", ipk), SHARED_KEEP_S + 3600)
            await p.execute()
        except Exception:  # noqa: BLE001 — успешный вход не отменяем из-за сбоя вспомогательной записи
            log.warning("Не удалось записать успешный вход в счётчики защиты", exc_info=True)

    async def reset_account(self, login: str) -> None:
        """Восстановление доступа администратором сервера (admin-reset): счётчики неудач этой записи и её пар «запись + адрес» очищаются; блокировки адресов и их история остаются."""
        ah = _h(account_key(login))
        keys = [self._k("f", "acct", ah)] + [k async for k in self.r.scan_iter(match=self._k("f", "pair", ah, "*"), count=200)]
        await self.r.delete(*keys)

    async def stats(self) -> dict:
        """Короткая сводка для админки: сколько адресов заблокировано сейчас."""
        blocks = await self.list_blocks()
        return {"blocked_now": len(blocks)}
