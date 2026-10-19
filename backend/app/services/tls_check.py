"""Проверка публичного HTTPS-адреса «глазами клиента»: системное доверие, обязательная проверка имени, разбор цепочки и указание, ГДЕ нужно исправление.

Сертификат принимается, только если его принял бы обычный клиент с тем же хранилищем доверия. Соединение без проверки (CERT_NONE) используется ИСКЛЮЧИТЕЛЬНО для чтения
отдаваемой цепочки (см. tls_chain.py); результат «успешно» от него никогда не бывает, и ничего не добавляется в доверенные.
"""
from __future__ import annotations

import socket
import ssl

from cryptography import x509

from . import tls_chain as tc

EXPIRING_DAYS = 14


def _gaierror(exc: OSError) -> bool:
    return isinstance(exc, socket.gaierror)


def probe(host: str, port: int = 443, timeout: float = 6.0, context: ssl.SSLContext | None = None, fetch_aia: bool = False, fetcher=tc.fetch_aia) -> dict:
    """state: ok | expiring | expired | name_mismatch | self_signed | incomplete_chain | untrusted | unreachable. ok: True / None (предупреждение, нет данных или исправление не здесь) / False.
    fix: где исправлять (reverse_proxy | organization_trust | certificate | dns_or_network). Блокирующая (в потоке)."""
    ctx = context or tc.system_context()
    base: dict = {"host": host, "port": port, "chain": [], "chain_complete": None, "trusted_by_server": None, "cause": None, "fix": None}
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
            der = s.getpeercert(binary_form=True) or b""
            try:
                ders = [c.public_bytes(ssl._ssl.ENCODING_DER) for c in s._sslobj.get_verified_chain()]
            except Exception:  # noqa: BLE001
                ders = [der]
        chain = [x509.load_der_x509_certificate(d) for d in ders]
        return _ok_result(base, chain, host)
    except ssl.SSLCertVerificationError as exc:
        return _failed_result(base, host, port, timeout, ctx, exc, fetch_aia, fetcher)
    except ssl.SSLError as exc:
        return {**base, "state": "untrusted", "ok": False, "message": f"TLS-соединение с {host}:{port} не установлено: {exc.reason or type(exc).__name__}.",
                "fix": {"where": "reverse_proxy", "text": "Проверьте настройки HTTPS на том устройстве, которое принимает соединения по этому адресу (протоколы TLS, сертификат)."}}
    except OSError as exc:
        reason = "dns" if _gaierror(exc) else "timeout" if isinstance(exc, TimeoutError) else "refused" if isinstance(exc, ConnectionRefusedError) else "network"
        why = {"dns": "имя не разрешается из этой сети (DNS)", "timeout": "нет ответа (таймаут)", "refused": "соединение отклонено", "network": exc.strerror or type(exc).__name__}[reason]
        return {**base, "state": "unreachable", "ok": None, "reason": reason,
                "message": f"Внешняя проверка с сервера недоступна: {host}:{port} — {why}. Возможно, у сервера нет маршрута к собственному публичному адресу; "
                           "это не означает, что адрес недоступен клиентам — проверьте его с рабочей станции.",
                "fix": {"where": "dns_or_network", "text": "Если клиенты открывают адрес, а сервер нет — особенность внутренней маршрутизации (split DNS, hairpin NAT). Для проверок можно задать внутренний адрес."}}


def _chain_info(chain: list[x509.Certificate]) -> list[dict]:
    return [tc.describe(c, "leaf" if i == 0 else "root" if c.subject == c.issuer else "intermediate") for i, c in enumerate(chain)]


def _ok_result(base: dict, chain: list[x509.Certificate], host: str) -> dict:
    leaf = tc.describe(chain[0], "leaf")
    left = leaf["days_left"]
    state = "expired" if left < 0 else "expiring" if left <= EXPIRING_DAYS else "ok"
    msg = (f"Сертификат {host} действителен ещё {left} дн., выдан: {leaf['issuer'] or '—'}; цепочка проверена по хранилищу доверия сервера." if state == "ok" else
           f"Сертификат {host} истекает через {left} дн. — обновите." if state == "expiring" else f"Сертификат {host} просрочен.")
    return {**base, "issuer": leaf["issuer"], "subject": leaf["subject"], "not_after": leaf["not_after"], "days_left": left, "chain": _chain_info(chain),
            "chain_complete": True, "trusted_by_server": True, "state": state, "ok": True if state == "ok" else None if state == "expiring" else False, "message": msg,
            "_chain_pem": tc.to_pem(chain)}


def _failed_result(base: dict, host: str, port: int, timeout: float, ctx: ssl.SSLContext, exc: ssl.SSLCertVerificationError, fetch_aia: bool, fetcher) -> dict:
    msg = exc.verify_message or str(exc)
    try:
        presented = tc.presented_chain(host, port, timeout)
    except (OSError, ssl.SSLError):
        presented = []
    out: dict = {**base, "verify_message": msg, "trusted_by_server": False}
    if not presented:
        return {**out, "state": "untrusted", "ok": False, "message": f"Сертификат {host} не проходит проверку: {msg}."}
    leaf = tc.describe(presented[0], "leaf")
    out.update(issuer=leaf["issuer"], subject=leaf["subject"], not_after=leaf["not_after"], days_left=leaf["days_left"], chain=_chain_info(presented), _chain_pem=tc.to_pem(presented))
    low = msg.lower()
    roots = tc.system_roots(ctx)
    if leaf["days_left"] < 0 or "expired" in low:
        return {**out, "state": "expired", "ok": False, "cause": "expired", "message": f"Сертификат {host} просрочен.",
                "fix": {"where": "certificate", "text": "Выпустите и установите новый сертификат на устройстве, где завершается HTTPS."}}
    if "hostname" in low or "ip address mismatch" in low or not tc.name_matches(presented[0], host):
        return {**out, "state": "name_mismatch", "ok": False, "cause": "name_mismatch", "message": f"Имя сайта {host} отсутствует в сертификате (выдан для {leaf['subject']}). Outlook не загрузит надстройку.",
                "fix": {"where": "certificate", "text": "Нужен сертификат, в котором перечислено имя публичного адреса (поле SAN)."}}
    if presented[0].subject == presented[0].issuer:
        return {**out, "state": "self_signed", "ok": False, "cause": "self_signed", "message": f"Сертификат {host} самоподписанный: ни серверы, ни клиенты по умолчанию ему не доверяют.",
                "fix": {"where": "certificate", "text": "Используйте сертификат публичного или корпоративного центра сертификации; самоподписанный сертификат Outlook и Exchange не примут."}}
    out["chain_complete"] = False if not tc._verify(presented[0], presented[1:], roots, host) else True
    completed = tc.complete_via_aia(presented, roots, host, fetcher) if fetch_aia else None
    if completed:
        return {**out, "state": "incomplete_chain", "ok": None, "cause": "incomplete_chain", "completed_chain_pem": tc.to_pem(completed),
                "message": f"Устройство, завершающее HTTPS для {host}, отдаёт неполную цепочку: нет промежуточного сертификата. Браузеры Windows докачивают его сами, а curl, Java, Python и "
                           "часть почтовых клиентов — нет. Недостающие общедоступные сертификаты получены из AIA и проверены до корня системного хранилища.",
                "fix": {"where": "reverse_proxy", "downloads": ["fullchain"], "text": "На reverse proxy / WAF / балансировщике (устройстве, где установлен сертификат сайта) укажите ПОЛНУЮ цепочку: сертификат сайта + промежуточные "
                                                                                              "(файл fullchain.pem можно скачать). Корневой сертификат в цепочку не включается."}}
    top = presented[-1]
    return {**out, "state": "untrusted", "ok": None, "cause": "private_ca",
            "message": (f"Этот сервер не доверяет цепочке сертификата {host} ({msg}). Если рабочие станции открывают адрес без предупреждений — центр сертификации организации "
                        "установлен у них (например, групповой политикой), а у серверного окружения Peregovorka — нет. Это не ошибка надстройки, но проверки с сервера будут неполными."
                        if not fetch_aia else
                        f"Этот сервер не доверяет цепочке сертификата {host} ({msg}), недостающих общедоступных промежуточных сертификатов не найдено. "
                        "Вероятно, используется внутренний центр сертификации организации."),
            "fix": {"where": "organization_trust", "downloads": ["chain"], "ca_subject": tc.describe(top, "root")["subject"],
                    "text": "Доверие внутреннему центру сертификации разворачивается штатными средствами организации (групповая политика, MDM) на рабочих станциях и на Exchange. "
                            "Для проверок с сервера Peregovorka администратор может явно подключить файл корневого сертификата организации (OUTLOOK_CHECK_CA_FILE). "
                            "Копирование сертификата самого сайта в доверенные исправлением не является."}}
