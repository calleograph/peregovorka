"""Инструкция для инженера АТС: формируется из фактических настроек этой установки Peregovorka и выбранного SIP-профиля.

Чистая функция без обращений к сети и базе: параметры передаёт вызывающий (`api/admin_sip.py`). Секреты (пароль профиля, ключи) в инструкцию не попадают —
их передают отдельным защищённым каналом. Чего приложение знать не может (внутренний адрес сервера в сети АТС, брандмауэр между сетями), помечено «уточните».
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

SUPPORTED_CODECS = ("PCMU (G.711 µ-law)", "PCMA (G.711 A-law)", "G.722", "G.729", "Opus")
TLS_NOTE = ("Входящий SIP по TLS на стороне Peregovorka в этой установке не настроен: служба SIP слушает только UDP/TCP. TLS работает для исходящих вызовов к АТС "
            "(Peregovorka — клиент TLS, сертификат АТС не проверяется при диагностике). Если нужен входящий TLS, он настраивается отдельно в конфигурации службы SIP.")


def server_address(settings: Any) -> tuple[str, str]:
    """(адрес для АТС, откуда взят). Приоритет: объявляемый в SDP адрес → адрес узла LiveKit → хост публичного адреса приложения."""
    for value, source in ((getattr(settings, "sip_media_ip", ""), "SIP_MEDIA_IP"), (getattr(settings, "livekit_node_ip", ""), "LIVEKIT_NODE_IP")):
        if value and value.strip():
            return value.strip(), source
    host = urlsplit(getattr(settings, "app_public_url", "") or "").hostname or ""
    return host, "публичный адрес приложения" if host else ""


def _transport_label(profile: dict) -> str:
    return (profile.get("transport") or "udp").upper()


def build_guide(settings: Any, profile: dict | None, extensions: list[dict], *, version: str = "") -> dict:
    """Структура инструкции: параметры, таблица сетевых правил, схема, текст для инженера, вопросы к инженеру, чек-лист."""
    sig = int(getattr(settings, "sip_signaling_port", 5060))
    rtp_a, rtp_b = int(getattr(settings, "sip_rtp_start", 20000)), int(getattr(settings, "sip_rtp_end", 20100))
    host, host_src = server_address(settings)
    cidrs = [c for c in re.split(r"[\s,;]+", getattr(settings, "sip_allowed_cidrs", "") or "") if c]
    p = profile or {}
    direction = p.get("direction", "both")
    inbound = direction in ("inbound", "both")
    outbound = direction in ("outbound", "both")
    pbx_addr = f"{p['host']}:{p['port']}" if p.get("host") else ""
    pbx_nets = list(p.get("allowed_addresses") or []) or cidrs
    transport = _transport_label(p) if p else "UDP"
    media_enc = {"disable": "не используется", "allow": "допускается (SRTP, если АТС его предлагает)", "require": "обязательно (SRTP)"}.get(p.get("media_encryption", "disable"), "не используется")
    codecs = list(p.get("codecs") or [])

    params = [
        {"key": "Адрес Peregovorka для АТС", "value": host or "не определён — задайте SIP_MEDIA_IP или публичный адрес", "note": f"источник: {host_src}" if host_src else "уточните видимый из сети АТС адрес сервера"},
        {"key": "SIP URI", "value": f"sip:{host}:{sig}" + (";transport=tcp" if transport == "TCP" else "") if host else "—", "note": "куда АТС направляет вызовы"},
        {"key": "Порт сигнализации", "value": f"{sig} (UDP и TCP)", "note": "SIP_SIGNALING_PORT"},
        {"key": "Транспорт профиля", "value": transport, "note": "в профиле «" + p["name"] + "»" if p.get("name") else "профиль не выбран"},
        {"key": "Диапазон RTP", "value": f"{rtp_a}–{rtp_b} (UDP)", "note": f"около {max(0, (rtp_b - rtp_a + 1)) // 2} одновременных вызовов (пара портов на вызов)"},
        {"key": "Объявляемый в SDP адрес", "value": getattr(settings, "sip_media_ip", "") or getattr(settings, "livekit_node_ip", "") or "не задан",
         "note": "должен быть достижим с АТС; при NAT задайте SIP_MEDIA_IP"},
        {"key": "Адрес АТС (из профиля)", "value": pbx_addr or "профиль не выбран", "note": "куда Peregovorka отправляет исходящие вызовы" if outbound else ""},
        {"key": "Разрешённые адреса АТС", "value": ", ".join(pbx_nets) or "не заданы", "note": "входящие принимаются только с них" if p.get("allowed_addresses") else ("из SIP_ALLOWED_CIDRS" if cidrs else "для входящих рекомендуется задать")},
        {"key": "Аутентификация", "value": ("логин «" + p["username"] + "» + пароль" if p.get("username") else "по IP-адресу (без логина)") if p else "—",
         "note": "пароль передаётся отдельно" if p.get("username") else "сопоставление входящего по IP (identify в PJSIP)"},
        {"key": "Realm / домен", "value": p.get("realm") or "—", "note": ""},
        {"key": "Caller ID исходящих", "value": p.get("caller_id") or "не задан", "note": "многие АТС отклоняют вызов без разрешённого номера" if not p.get("caller_id") else ""},
        {"key": "Кодеки", "value": ", ".join(codecs) if codecs else "по умолчанию LiveKit (PCMU, PCMA, G.722, Opus)", "note": "список должен пересекаться со списком на АТС"},
        {"key": "Шифрование медиа (SRTP)", "value": media_enc, "note": ""},
        {"key": "DTMF", "value": "RFC 2833 / RFC 4733 (RTP events)", "note": "SIP INFO не используется; на АТС включите rfc4733 (dtmf_mode)"},
        {"key": "TLS", "value": "исходящие: поддерживается; входящие: не настроено", "note": TLS_NOTE},
    ]

    rows: list[dict] = []
    if inbound or not p:
        rows += [
            {"src": "АТС", "dst": "Peregovorka SIP", "proto": "UDP/TCP", "port": str(sig), "purpose": "SIP-сигнализация (INVITE, ACK, BYE, CANCEL, OPTIONS)", "scope": "pbx", "required": True},
            {"src": "АТС", "dst": "Peregovorka SIP", "proto": "UDP", "port": f"{rtp_a}–{rtp_b}", "purpose": "RTP-аудио (АТС → Peregovorka)", "scope": "pbx", "required": True},
        ]
    if outbound or not p:
        rows += [
            {"src": "Peregovorka SIP", "dst": "АТС", "proto": transport if p else "UDP/TCP/TLS", "port": str(p.get("port") or "порт из профиля"), "purpose": "Исходящие вызовы: SIP-сигнализация", "scope": "pbx", "required": True},
            {"src": "Peregovorka SIP", "dst": "АТС", "proto": "UDP", "port": "диапазон RTP АТС (уточните)", "purpose": "RTP-аудио (Peregovorka → АТС); АТС сообщает порт в SDP", "scope": "pbx", "required": True},
            {"src": "АТС", "dst": "Peregovorka SIP", "proto": "UDP", "port": f"{rtp_a}–{rtp_b}", "purpose": "RTP-аудио (АТС → Peregovorka): ответный поток исходящего вызова", "scope": "pbx", "required": True},
        ]
    rows += [
        {"src": "служба SIP", "dst": "LiveKit", "proto": "TCP", "port": "7880", "purpose": "управление вызовами (внутри сети контейнеров)", "scope": "internal", "required": False},
        {"src": "служба SIP, LiveKit", "dst": "Redis", "proto": "TCP", "port": "6379", "purpose": "общая шина LiveKit ↔ SIP (внутри сети контейнеров)", "scope": "internal", "required": False},
    ]

    asks = ["Адрес и порт АТС (и запасной адрес, если есть).", "Тип SIP-транка: регистрация на АТС или транк по IP.", "Способ аутентификации: логин и пароль либо сопоставление по IP.",
            "Выделенные номера и внутренние добавочные для комнат Peregovorka.", "Список IP-адресов АТС (откуда приходят SIP и RTP).", "Разрешённые исходящие направления (местные, междугородные, международные).",
            "Кодеки, которые поддерживает транк, и их порядок предпочтения.", "Режим DTMF (rfc4733 / inband / info).", "Диапазон RTP на АТС и наличие NAT между АТС и Peregovorka.",
            "Правила Caller ID: какой номер АТС примет в исходящем вызове.", "Нужны ли TLS и SRTP, чем выдан сертификат АТС."]

    nums = [e for e in extensions if e.get("extension")]
    ext_text = "; ".join(f"{e['extension']} → комната «{e['room']}»" for e in nums[:30]) or "не заданы (внутренний номер задаётся в настройках комнаты → Телефония)"
    lines = ["ПОДКЛЮЧЕНИЕ АТС К PEREGOVORKA", f"(сформировано автоматически по настройкам сервера{', версия ' + version if version else ''})", ""]
    lines += ["1. Куда направлять вызовы", f"   Адрес сервера Peregovorka: {host or '— (уточните у администратора Peregovorka)'}", f"   SIP URI: {params[1]['value']}", f"   Порт SIP: {sig} (UDP и TCP)",
              f"   RTP: UDP {rtp_a}–{rtp_b}", ""]
    lines += ["2. Откуда Peregovorka звонит на АТС"] + ([f"   АТС: {pbx_addr}, транспорт {transport}", f"   Логин: {p.get('username') or 'нет (по IP)'} (пароль передаётся отдельно)",
                                                      f"   Caller ID: {p.get('caller_id') or 'не задан'}"] if p else ["   (профиль не выбран — выберите профиль для точных параметров)"]) + [""]
    lines += ["3. Какие номера направить в Peregovorka", f"   {ext_text}", f"   Принимаемые профилем номера: {', '.join(p.get('inbound_numbers') or []) or 'любые'}", ""]
    lines += ["4. Сетевые правила (настройте на межсетевых экранах)"]
    for r in rows:
        if r["scope"] == "pbx":
            lines.append(f"   {r['src']} → {r['dst']}: {r['proto']} {r['port']} — {r['purpose']}")
    lines += ["   Внутренние соединения контейнеров наружу не открывать.", ""]
    lines += ["5. Параметры совместимости", f"   Кодеки: {params[11]['value']}", "   DTMF: RFC 2833/4733", f"   SRTP: {media_enc}",
              f"   Разрешённые адреса АТС: {', '.join(pbx_nets) or 'не заданы'}", "   NAT: если сервер Peregovorka за NAT, адрес в SDP задаётся SIP_MEDIA_IP; на АТС отключите direct_media (для Asterisk: direct_media=no, rtp_symmetric=yes).", ""]
    lines += ["6. Проверка после настройки",
              "   а) АТС отвечает на OPTIONS от Peregovorka; б) тестовый вызов из «Администрирование → Интеграции → SIP-телефония» проходит; в) звонок на добавочный комнаты слышен в обе стороны.", "",
              "Пароли и ключи в этот документ не включены."]

    return {
        "profile": {"id": p.get("id"), "name": p.get("name")} if p else None,
        "params": params, "network": rows, "ask": asks, "text": "\n".join(lines), "tls_note": TLS_NOTE,
        "checklist": ["Порты SIP и RTP открыты на брандмауэре только с адресов АТС", "На АТС создан транк/endpoint для Peregovorka (адрес, порт, транспорт совпадают с профилем)",
                      "Кодеки на АТС и в профиле пересекаются", "Внутренние номера комнат маршрутизируются на Peregovorka", "Исходящие номера Peregovorka разрешены на АТС (caller ID, направления)",
                      "Тестовый вызов выполнен, звук слышен в обе стороны", "Пароли и ключи переданы по защищённому каналу"],
        "diagram": {"nodes": [{"id": "phone", "label": "SIP-телефон"}, {"id": "pbx", "label": "АТС" + (f" ({p['host']})" if p.get("host") else "")},
                              {"id": "sip", "label": "Peregovorka SIP (livekit-sip)"}, {"id": "lk", "label": "LiveKit"}, {"id": "room", "label": "Участники конференции"}],
                    "links": [{"from": "phone", "to": "pbx", "label": "SIP/RTP внутри сети АТС", "firewall": False},
                              {"from": "pbx", "to": "sip", "label": f"SIP {sig} UDP/TCP · RTP {rtp_a}–{rtp_b} UDP", "firewall": True},
                              {"from": "sip", "to": "lk", "label": "ws 7880 + Redis (внутри контейнеров)", "firewall": False},
                              {"from": "lk", "to": "room", "label": "WebRTC (DTLS-SRTP), порты LiveKit RTC", "firewall": False}]},
    }
