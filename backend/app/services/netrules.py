"""Сетевые требования установленного экземпляра: какие порты нужно открыть, куда и зачем.

Только стандартная библиотека, без импорта `app.*`: модуль одинаково работает внутри backend (раздел «Сетевая доступность») и на хосте
(`scripts/ports.sh` читает `.env` и загружает этот файл по пути), в том числе когда веб-интерфейс и контейнеры не запущены.
Перечень строится из фактической конфигурации (`NetInput`), а не из зашитой таблицы: порты, адрес узла, режим TLS, телефония и
подключённые каталоги берутся из `.env`/настроек. Что в настройках не видно, в перечень не попадает и перечислено в `limits`.
"""
from __future__ import annotations

import csv
import io
import ipaddress
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# Ключи `.env` → поля NetInput (имя в контейнере backend у части ключей другое, поэтому принимаются оба)
_ALIASES = {
    "app_public_url": ("APP_PUBLIC_URL",),
    "livekit_public_url": ("LIVEKIT_PUBLIC_URL",),
    "node_ip": ("LIVEKIT_NODE_IP",),
    "lk_tcp": ("LIVEKIT_TCP_PORT", "LIVEKIT_RTC_TCP_PORT"),
    "lk_udp": ("LIVEKIT_UDP_PORT", "LIVEKIT_RTC_UDP_PORT"),
    "lk_bind": ("LIVEKIT_BIND_ADDR", "NET_LIVEKIT_BIND_ADDR"),
    "lk_http": ("LIVEKIT_HTTP_PORT", "NET_LIVEKIT_HTTP_PORT"),
    "web_bind": ("WEB_BIND_ADDR", "NET_WEB_BIND_ADDR"),
    "web_port": ("WEB_PORT", "NET_WEB_PORT"),
    "nginx_port": ("NGINX_LISTEN_PORT", "NET_NGINX_LISTEN_PORT"),
    "tls_cert": ("NGINX_TLS_CERT", "TLS_LOCAL_CONFIGURED"),
    "profile": ("INSTALL_PROFILE", "NET_INSTALL_PROFILE"),
    "sip_enabled": ("SIP_ENABLED",),
    "sip_port": ("SIP_SIGNALING_PORT",),
    "sip_rtp_start": ("SIP_RTP_START",),
    "sip_rtp_end": ("SIP_RTP_END",),
    "sip_bind": ("SIP_BIND_ADDR", "NET_SIP_BIND_ADDR"),
    "sip_cidrs": ("SIP_ALLOWED_CIDRS",),
    "ldap_uris": ("LDAP_URIS",),
}


@dataclass
class NetInput:
    app_public_url: str = ""
    livekit_public_url: str = ""
    node_ip: str = ""
    lk_tcp: int = 0
    lk_udp: int = 0
    lk_bind: str = "0.0.0.0"
    lk_http: int = 0
    web_bind: str = "127.0.0.1"
    web_port: int = 0
    nginx_port: int = 0
    tls_local: bool = False
    profile: str = ""
    sip_enabled: bool = False
    sip_port: int = 5060
    sip_rtp_start: int = 20000
    sip_rtp_end: int = 20100
    sip_bind: str = "0.0.0.0"
    sip_cidrs: str = ""
    # исходящие соединения, о которых известно только из настроек в базе (профили LDAP, почта, SMB, Exchange): {"what", "host", "port", "proto", "why"}
    outbound: list[dict[str, Any]] = field(default_factory=list)
    # какие сведения не удалось получить (например, нет доступа к базе при запуске с хоста)
    unknown: list[str] = field(default_factory=list)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def from_env(env: dict[str, str], outbound: list[dict[str, Any]] | None = None, unknown: list[str] | None = None) -> NetInput:
    """Собирает вход из словаря переменных (`.env` хоста или окружение контейнера)."""
    def pick(name: str, default: str = "") -> str:
        for key in _ALIASES[name]:
            if env.get(key, "").strip():
                return env[key].strip()
        return default

    n = NetInput(
        app_public_url=pick("app_public_url"), livekit_public_url=pick("livekit_public_url"), node_ip=pick("node_ip"),
        lk_tcp=_int(pick("lk_tcp")), lk_udp=_int(pick("lk_udp")), lk_bind=pick("lk_bind", "0.0.0.0"), lk_http=_int(pick("lk_http")),
        web_bind=pick("web_bind", "127.0.0.1"), web_port=_int(pick("web_port")), nginx_port=_int(pick("nginx_port")),
        tls_local=bool(pick("tls_cert")) and pick("tls_cert").lower() not in ("0", "false", "no"),
        profile=pick("profile"), sip_enabled=_truthy(pick("sip_enabled")), sip_port=_int(pick("sip_port"), 5060),
        sip_rtp_start=_int(pick("sip_rtp_start"), 20000), sip_rtp_end=_int(pick("sip_rtp_end"), 20100),
        sip_bind=pick("sip_bind", "0.0.0.0"), sip_cidrs=pick("sip_cidrs"),
        outbound=list(outbound or []), unknown=list(unknown or []),
    )
    for uri in [u.strip() for u in pick("ldap_uris").split(",") if u.strip()]:
        p = urlsplit(uri)
        if p.hostname:
            n.outbound.append({"what": "Каталог (LDAP_URIS)", "host": p.hostname, "port": p.port or (636 if p.scheme == "ldaps" else 389),
                               "proto": "TCP", "why": "вход доменных пользователей и поиск в каталоге"})
    return n


def parse_dotenv(text: str) -> dict[str, str]:
    """`KEY=VALUE` без подстановок; кавычки по краям снимаются, комментарии `#` в начале строки пропускаются."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key.replace("_", "").isalnum():
            out[key] = val
    return out


def _url_port(url: str) -> tuple[str, int, str]:
    p = urlsplit(url)
    scheme = (p.scheme or "").lower()
    default = {"https": 443, "wss": 443, "http": 80, "ws": 80}.get(scheme, 0)
    try:
        port = p.port or default
    except ValueError:
        port = default
    return (p.hostname or "").lower(), port, scheme


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


# Диапазоны для документации (RFC 5737) в примерах изображают публичные адреса, поэтому «частными» не считаются
_DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]


def _private(host: str) -> bool | None:
    try:
        a = ipaddress.ip_address(host)
    except ValueError:
        return None
    if any(a in net for net in _DOC_NETS if net.version == a.version):
        return False
    return a.is_private or a.is_loopback or a.is_link_local or a.is_unspecified


def _listen_scope(bind: str) -> str:
    """Кто может подключиться по настройке привязки: loopback — только сам сервер."""
    try:
        return "local" if ipaddress.ip_address(bind).is_loopback else "network"
    except ValueError:
        return "network"


def _rule(rid: str, direction: str, scope: str, required: str, proto: str, port: str, component: str, purpose: str,
          where: str, notes: list[str] | None = None) -> dict[str, Any]:
    return {"id": rid, "direction": direction, "scope": scope, "required": required, "proto": proto, "port": port,
            "component": component, "purpose": purpose, "where": where, "notes": notes or []}


def build(n: NetInput) -> dict[str, Any]:
    """Перечень правил для этого экземпляра: `variant` (как установлен), `rules`, `local_only` (что публиковать нельзя), `warnings`, `limits`."""
    host, https_port, scheme = _url_port(n.app_public_url)
    rules: list[dict[str, Any]] = []
    warnings: list[str] = []
    variant: list[str] = []
    n_unknown = list(n.unknown)

    if not n.app_public_url or not host:
        warnings.append("APP_PUBLIC_URL не задан или некорректен: адрес и порт входа определить нельзя.")
    elif scheme != "https":
        warnings.append("APP_PUBLIC_URL не использует HTTPS: браузер не даст доступ к микрофону и экрану (исключение — localhost).")
    if host and host in ("localhost", "127.0.0.1", "::1"):
        warnings.append("APP_PUBLIC_URL указывает на localhost: пользователи не смогут открыть сервис по этому адресу.")

    # --- как терминируется HTTPS
    if n.tls_local:
        variant.append("TLS завершает nginx, установленный вместе с Peregovorka (собственный сертификат)")
        tls_where = "nginx сервера"
    else:
        variant.append("TLS завершает внешний reverse proxy или nginx хоста")
        tls_where = "внешний reverse proxy"
    if n.profile == "shared-host":
        variant.append("общий сервер (shared-host): nginx и порт 443 принадлежат уже работающему сайту, Peregovorka подключается к нему")
    elif n.profile == "standalone":
        variant.append("выделенный сервер (standalone)")

    # --- входящие из интернета / от клиентов
    pub_port = https_port or 443
    rules.append(_rule(
        "https", "in", "internet", "required", "TCP", str(pub_port), tls_where,
        "Страница, API, WebSocket событий, сигнальное соединение звонков (/livekit/), редактор доски (/drawio/), гостевые ссылки.",
        f"клиенты → {host or 'публичный адрес сервиса'}",
        ["Если сотрудники и гости подключаются только из локальной сети, публиковать порт в интернет не нужно.",
         "Весь этот трафик — один HTTPS-порт: отдельного порта для сигнального соединения LiveKit нет."]))

    if n.livekit_public_url:
        lhost, lport, lscheme = _url_port(n.livekit_public_url)
        if lhost and (lhost != host or (lport and lport != https_port)):
            variant.append("отдельный публичный адрес сигнального соединения LiveKit (LIVEKIT_PUBLIC_URL отличается от APP_PUBLIC_URL)")
            rules.append(_rule(
                "livekit-signal", "in", "internet", "required", "TCP", str(lport or 443), "LiveKit (через свой прокси)",
                "Сигнальное соединение звонков (WebSocket) на отдельном адресе.", f"клиенты → {lhost}",
                ["Адрес отличается от основного: на нём тоже нужен корректный сертификат и проброс WebSocket (Upgrade)."]))

    node = n.node_ip
    where_rtc = f"клиенты → {node or 'LIVEKIT_NODE_IP'}"
    rtc_notes = [
        "LiveKit сообщает клиентам адрес LIVEKIT_NODE_IP и этот же номер порта: на NAT порт пробрасывается на сервер без смены номера.",
        "Через HTTP-прокси медиа не ходит: трафик идёт напрямую на сервер.",
        "Встроенного TURN/STUN в установке нет и внешние STUN/TURN не используются: клиенты должны достучаться до этих портов напрямую.",
    ]
    if n.lk_udp:
        rules.append(_rule("rtc-udp", "in", "internet", "required", "UDP", str(n.lk_udp), "LiveKit (ICE/UDP, один порт на всех)",
                           "Звук, видео и показ экрана (WebRTC) — основной путь.", where_rtc, rtc_notes))
    else:
        warnings.append("Порт LiveKit UDP не определён (LIVEKIT_UDP_PORT): правило не построено.")
    if n.lk_tcp:
        rules.append(_rule("rtc-tcp", "in", "internet", "recommended", "TCP", str(n.lk_tcp), "LiveKit (ICE/TCP)",
                           "Запасной путь медиа, когда у клиента закрыт UDP (например, строгие корпоративные сети).", where_rtc,
                           ["Без этого порта пользователи за фильтрующим UDP межсетевым экраном не смогут говорить.",
                            "Качество по TCP хуже при потерях пакетов."]))
    else:
        warnings.append("Порт LiveKit TCP не определён (LIVEKIT_TCP_PORT): правило не построено.")

    if not node:
        warnings.append("LIVEKIT_NODE_IP не задан: клиенты не получат адрес для медиа.")
    else:
        priv = _private(node)
        if priv is None:
            warnings.append("LIVEKIT_NODE_IP — не IP-адрес: LiveKit ожидает именно IP.")
        elif priv:
            warnings.append(
                f"LIVEKIT_NODE_IP ({node}) — частный адрес: клиентам из интернета он недоступен. Для доступа извне укажите во "
                "LIVEKIT_NODE_IP внешний адрес (на межсетевом экране пробросьте порты на сервер) и проверьте, что клиенты из локальной "
                "сети достают до него (hairpin NAT или внутренняя маршрутизация).")
            variant.append("медиа за NAT или только для локальной сети (LIVEKIT_NODE_IP частный)")
    if n.lk_bind and _listen_scope(n.lk_bind) == "local" and (n.lk_tcp or n.lk_udp):
        warnings.append("LIVEKIT_BIND_ADDR привязан к loopback: порты медиа недоступны с других компьютеров, звонки работать не будут.")

    # --- телефония
    if n.sip_enabled:
        variant.append("включена SIP-телефония")
        src = f"АТС ({n.sip_cidrs})" if n.sip_cidrs else "АТС или оператор (ограничьте адрес источника)"
        rules.append(_rule("sip-sig-udp", "in", "pbx", "conditional", "UDP", str(n.sip_port), "LiveKit SIP", "Сигнализация SIP от АТС.", f"{src} → сервер", ["Нужна только при телефонии; в интернет без ограничения источника не публикуйте."]))
        rules.append(_rule("sip-sig-tcp", "in", "pbx", "conditional", "TCP", str(n.sip_port), "LiveKit SIP", "Сигнализация SIP по TCP.", f"{src} → сервер"))
        rules.append(_rule("sip-rtp", "in", "pbx", "conditional", "UDP", f"{n.sip_rtp_start}-{n.sip_rtp_end}", "LiveKit SIP", "Звук телефонных вызовов (RTP).", f"{src} → сервер",
                           ["Диапазон должен совпадать на АТС и на межсетевом экране; адрес для SDP — SIP_MEDIA_IP или LIVEKIT_NODE_IP."]))
    else:
        variant.append("телефония выключена: порты SIP не публикуются")

    # --- исходящие
    rules.append(_rule("dns", "out", "internal", "required", "UDP+TCP", "53", "сервер", "Разрешение имён (домен контроллеров, почты, репозиториев при обновлении).", "сервер → DNS организации"))
    for ob in n.outbound:
        rules.append(_rule(
            "out-" + str(ob.get("what", "")).lower().replace(" ", "-")[:30] + "-" + str(ob.get("port", "")), "out", "internal", "conditional",
            str(ob.get("proto", "TCP")), str(ob.get("port", "")), str(ob.get("what", "")), str(ob.get("why", "")), f"сервер → {ob.get('host', '')}"))
    rules.append(_rule("updates", "out", "internet", "conditional", "TCP", "443 (и 80)", "установка и обновление",
                       "GitHub, PyPI, npm, Docker Hub, apt-зеркала, источник моделей — только при установке и обновлении; в работе нужна только локальная сеть.",
                       "сервер → интернет", ["Внешнего доступа в обратную сторону не требуется. В закрытом контуре обновление выполняется через прокси или зеркала организации."]))

    # --- только локально (наружу не публиковать)
    local_only: list[dict[str, Any]] = []
    if n.web_port:
        local_only.append({"proto": "TCP", "port": str(n.web_port), "component": "web (nginx контейнера)", "bind": n.web_bind or "127.0.0.1",
                           "purpose": "вход для nginx хоста или внешнего прокси", "reachable": "только сервер" if _listen_scope(n.web_bind or "127.0.0.1") == "local" else "сеть сервера"})
    if n.nginx_port:
        local_only.append({"proto": "TCP", "port": str(n.nginx_port), "component": "nginx хоста (site Peregovorka)", "bind": "по настройке nginx",
                           "purpose": "порт, на который внешний прокси передаёт трафик" if not n.tls_local else "внутренний порт сайта",
                           "reachable": "внешний прокси (ограничьте источник по адресу)" if not n.tls_local else "по настройке nginx"})
    if n.lk_http:
        local_only.append({"proto": "TCP", "port": str(n.lk_http), "component": "LiveKit (API и WebSocket)", "bind": "127.0.0.1", "purpose": "связь backend и web с LiveKit", "reachable": "только сервер"})
    local_only.append({"proto": "TCP", "port": "6789", "component": "LiveKit (метрики)", "bind": "сеть проекта", "purpose": "раздел «Производительность»", "reachable": "только контейнеры проекта"})
    local_only.append({"proto": "TCP", "port": "PostgreSQL, Redis, ASR, локальная LLM", "component": "внутренние службы", "bind": "сеть проекта", "purpose": "работа приложения", "reachable": "не публикуются"})

    limits = [
        "Перечень построен по настройкам этого экземпляра, а не по состоянию реальной сети: он не доказывает, что правила на межсетевом экране уже созданы.",
        "Проверка с самого сервера не подтверждает доступ из интернета: она не проходит внешний NAT и межсетевой экран. Достоверная проверка — вход в звонок с компьютера вне сети организации (раздел «Диагностика клиентов» покажет, шёл ли звук по UDP или TCP).",
        "UDP-порты нельзя проверить внешним запросом без участия клиента: устройство без ответа и закрытый порт выглядят одинаково.",
        "Адреса интеграций из настроек (почта, Exchange, SMB, внешние языковые модели, Bitrix24, получатели событий) видны только веб-разделу и команде с параметром --live; из файла .env их не определить.",
        "Одиночный экземпляр: несколько узлов LiveKit или отдельный TURN в штатной установке не поддерживаются — для такой схемы правила составляются вручную.",
    ]
    if n_unknown:
        limits.append("Не удалось определить: " + "; ".join(n_unknown) + ".")

    return {"variant": variant, "rules": rules, "local_only": local_only, "warnings": warnings, "limits": limits,
            "public_host": host, "node_ip": node}


# --- вывод

_DIR = {"in": "входящее", "out": "исходящее"}
_SCOPE = {"internet": "из интернета/от клиентов", "internal": "внутренняя сеть организации", "pbx": "только от АТС", "local": "только сервер"}
_SCOPE_OUT = {"internet": "в интернет", "internal": "во внутренней сети организации", "pbx": "к АТС", "local": "только сервер"}


def _scope(x: dict[str, Any]) -> str:
    return (_SCOPE_OUT if x["direction"] == "out" else _SCOPE)[x["scope"]]


_REQ = {"required": "обязательно", "recommended": "рекомендуется", "conditional": "если используется"}


def to_text(r: dict[str, Any]) -> str:
    lines = ["Сетевые требования Peregovorka", ""]
    lines += ["Вариант установки:"] + [f"  - {v}" for v in r["variant"]] + [""]
    for w in r["warnings"]:
        lines.append(f"ВНИМАНИЕ: {w}")
    if r["warnings"]:
        lines.append("")
    for title, d in (("ВХОДЯЩИЕ СОЕДИНЕНИЯ (на межсетевом экране перед сервером)", "in"), ("ИСХОДЯЩИЕ СОЕДИНЕНИЯ СЕРВЕРА", "out")):
        lines.append(title)
        for x in (x for x in r["rules"] if x["direction"] == d):
            lines.append(f"  {x['proto']:<8} {x['port']:<14} [{_REQ[x['required']]}] {x['component']}: {x['purpose']}")
            lines.append(f"           {x['where']} · {_scope(x)}")
            for note in x["notes"]:
                lines.append(f"           • {note}")
        lines.append("")
    lines.append("НЕ ПУБЛИКОВАТЬ (только внутри сервера)")
    for x in r["local_only"]:
        lines.append(f"  {x['proto']:<4} {x['port']:<16} {x['component']} — {x['purpose']} ({x['reachable']})")
    lines += ["", "ОГРАНИЧЕНИЯ ПРОВЕРКИ"] + [f"  - {x}" for x in r["limits"]]
    return "\n".join(lines) + "\n"


def to_markdown(r: dict[str, Any]) -> str:
    def esc(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", " ")
    out = ["# Сетевые требования Peregovorka", "", "## Вариант установки", ""] + [f"- {v}" for v in r["variant"]] + [""]
    if r["warnings"]:
        out += ["## Внимание", ""] + [f"- {w}" for w in r["warnings"]] + [""]
    for title, d in (("Входящие соединения (межсетевой экран перед сервером)", "in"), ("Исходящие соединения сервера", "out")):
        out += [f"## {title}", "", "| Протокол | Порт | Обязательность | Компонент | Назначение | Откуда → куда | Примечания |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for x in (x for x in r["rules"] if x["direction"] == d):
            out.append(f"| {x['proto']} | {x['port']} | {_REQ[x['required']]} | {esc(x['component'])} | {esc(x['purpose'])} | {esc(x['where'])} | {esc(' '.join(x['notes']))} |")
        out.append("")
    out += ["## Не публиковать (только внутри сервера)", "", "| Протокол | Порт | Компонент | Назначение | Кто достаёт |", "| --- | --- | --- | --- | --- |"]
    out += [f"| {x['proto']} | {x['port']} | {esc(x['component'])} | {esc(x['purpose'])} | {esc(x['reachable'])} |" for x in r["local_only"]]
    out += ["", "## Ограничения проверки", ""] + [f"- {x}" for x in r["limits"]]
    return "\n".join(out) + "\n"


def to_csv(r: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["направление", "протокол", "порт", "обязательность", "кто подключается", "компонент", "назначение", "откуда → куда", "примечания"])
    for x in r["rules"]:
        w.writerow([_DIR[x["direction"]], x["proto"], x["port"], _REQ[x["required"]], _scope(x), x["component"], x["purpose"], x["where"], " ".join(x["notes"])])
    return "﻿" + buf.getvalue()      # BOM: Excel сам распознаёт UTF-8


def firewall_request(r: dict[str, Any]) -> str:
    """Текст заявки сетевому администратору: что разрешить на периметре. Не команды для конкретного межсетевого экрана — их формат у каждого свой."""
    node = r.get("node_ip") or "<внешний адрес сервера>"
    lines = ["Прошу открыть для сервиса видеовстреч Peregovorka:", ""]
    for x in (x for x in r["rules"] if x["direction"] == "in" and x["scope"] in ("internet", "pbx")):
        tgt = r["public_host"] if x["id"] in ("https", "livekit-signal") and r["public_host"] else node
        src = "любых адресов (интернет)" if x["scope"] == "internet" else "адресов АТС (уточнить)"
        dest = f"на {x['component']}" if x["id"] in ("https", "livekit-signal") else "на сервер без смены номера порта"
        lines.append(f"  • {x['proto']} {x['port']}: от {src} → {tgt}, {dest}. {x['purpose']}")
    lines += ["", "Исходящие с сервера:"]
    for x in (x for x in r["rules"] if x["direction"] == "out" and x["id"] != "updates"):
        lines.append(f"  • {x['proto']} {x['port']}: {x['where']} — {x['purpose'].rstrip('.')}")
    lines += ["", "Остальные порты сервера наружу не публикуются."]
    return "\n".join(lines) + "\n"


def to_json(r: dict[str, Any]) -> str:
    return json.dumps(r, ensure_ascii=False, indent=2) + "\n"


FORMATS = {"text": to_text, "md": to_markdown, "csv": to_csv, "json": to_json, "request": firewall_request}
