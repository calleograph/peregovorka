"""Проверка публичного HTTPS-адреса «глазами клиента»: стандартная система доверия ОС и обязательная проверка имени сервера.

Сертификат принимается, только если его принял бы обычный клиент. Режим без проверки (CERT_NONE) используется ИСКЛЮЧИТЕЛЬНО для показа сведений о сертификате,
который проверку не прошёл, — результат от него никогда не бывает «успешно» и ничего не добавляется в доверенные.
"""
from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone

from cryptography import x509
from cryptography.x509.oid import NameOID

EXPIRING_DAYS = 14


def _name(n: x509.Name, oid) -> str:
    v = n.get_attributes_for_oid(oid)
    return v[0].value if v else ""


def _describe(der: bytes) -> dict:
    c = x509.load_der_x509_certificate(der)
    na = c.not_valid_after_utc if hasattr(c, "not_valid_after_utc") else c.not_valid_after.replace(tzinfo=timezone.utc)
    issuer = ", ".join(x for x in (_name(c.issuer, NameOID.COMMON_NAME), _name(c.issuer, NameOID.ORGANIZATION_NAME)) if x)
    return {"issuer": issuer, "subject": _name(c.subject, NameOID.COMMON_NAME), "not_after": na.isoformat(timespec="seconds"),
            "days_left": (na - datetime.now(timezone.utc)).days}


def _unverified_info(host: str, port: int, timeout: float) -> dict:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE        # только чтение сведений для показа; доверие не выдаётся
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
            return _describe(s.getpeercert(binary_form=True) or b"")
    except (OSError, ValueError):
        return {}


def probe(host: str, port: int = 443, timeout: float = 6.0, context: ssl.SSLContext | None = None) -> dict:
    """state: ok | expiring | expired | name_mismatch | untrusted | unreachable. ok: True / None (предупреждение или нет данных) / False. Блокирующая (в потоке)."""
    ctx = context or ssl.create_default_context()                      # системное хранилище доверия + check_hostname=True
    base = {"host": host, "port": port}
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
            info = _describe(s.getpeercert(binary_form=True) or b"")
    except ssl.SSLCertVerificationError as exc:
        msg = exc.verify_message or str(exc)
        low = msg.lower()
        state = "name_mismatch" if "hostname" in low or "ip address mismatch" in low else "expired" if "expired" in low else "untrusted"
        why = {"name_mismatch": "имя в сертификате не соответствует адресу", "expired": "срок действия истёк", "untrusted": f"цепочка не доверена ({msg})"}[state]
        return {**base, **_unverified_info(host, port, timeout), "state": state, "ok": False, "verify_message": msg,
                "message": f"Сертификат {host} не проходит проверку: {why}. Outlook не загрузит надстройку."}
    except ssl.SSLError as exc:
        return {**base, "state": "untrusted", "ok": False, "message": f"TLS-соединение с {host}:{port} не установлено: {exc.reason or type(exc).__name__}."}
    except OSError as exc:
        return {**base, "state": "unreachable", "ok": None,
                "message": f"Внешняя проверка с сервера недоступна: {host}:{port} не отвечает ({exc.strerror or type(exc).__name__}). "
                           "Возможно, у сервера нет маршрута к собственному публичному адресу — проверьте адрес с рабочей станции."}
    left = info["days_left"]
    state = "expired" if left < 0 else "expiring" if left <= EXPIRING_DAYS else "ok"
    msg = (f"Сертификат {host} действителен ещё {left} дн., выдан: {info['issuer'] or '—'}." if state == "ok" else
           f"Сертификат {host} истекает через {left} дн. — обновите." if state == "expiring" else f"Сертификат {host} просрочен.")
    return {**base, **info, "state": state, "ok": True if state == "ok" else None if state == "expiring" else False, "message": msg}
