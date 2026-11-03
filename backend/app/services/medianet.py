"""Адреса медиасоединений LiveKit (ICE): публичный адрес, режим, согласованные настройки и проверки.

Только стандартная библиотека, без импорта `app.*`: модуль одинаково работает в backend («Сетевая доступность → Медиасоединения») и на
хосте (`scripts/medianet.py` — установщик, обновление, проверка из терминала). Решение принимает одна функция `plan()` для всех вариантов
установки; `deployment/livekit/entrypoint.sh` только исполняет его в контейнере.

Почему так. LiveKit работает в сети Docker: «внутренний» адрес для него — адрес контейнера (172.x), недоступный клиентам. Поэтому за NAT
клиентам объявляются два адреса: публичный (его находит сам LiveKit через STUN — `use_external_ip`, или задаёт администратор) и адрес
сервера в локальной сети (`LIVEKIT_RTC_LAN_IP` добавляется на интерфейс контейнера, `advertise_internal_ip`). Пользователи из интернета
идут на публичный адрес через проброс портов, пользователи офиса — напрямую на сервер, без «петли» NAT на маршрутизаторе.

Режимы (`LIVEKIT_MEDIA_MODE`):
  direct      — публичный адрес прямо на интерфейсе сервера: он и объявляется, STUN не нужен;
  nat-stun    — сервер за NAT, публичный адрес находит LiveKit через STUN при каждом запуске (STUN не ответил — последний известный адрес);
  nat-manual  — сервер за NAT, публичный адрес задан администратором (`LIVEKIT_PUBLIC_IP=<IPv4>`);
  lan         — только локальная сеть (`LIVEKIT_PUBLIC_IP=off` или публичный адрес определить не удалось).
"""
from __future__ import annotations

import ipaddress
import os
import secrets
import socket
import struct
import time
from typing import Any

DEFAULT_STUN = "stun.l.google.com:19302,stun1.l.google.com:19302"
# ключи .env, которые вычисляет plan(); администратор задаёт LIVEKIT_NODE_IP, LIVEKIT_PUBLIC_IP, LIVEKIT_STUN_SERVERS и порты
DERIVED = ("LIVEKIT_MEDIA_MODE", "LIVEKIT_RTC_NODE_IP", "LIVEKIT_RTC_USE_EXTERNAL_IP", "LIVEKIT_RTC_ADVERTISE_INTERNAL", "LIVEKIT_RTC_LAN_IP")
MODE_TEXT = {
    "direct": "публичный адрес на интерфейсе сервера",
    "nat-stun": "сервер за NAT, публичный адрес определяется через STUN",
    "nat-manual": "сервер за NAT, публичный адрес задан администратором",
    "lan": "только локальная сеть",
}
SOURCE_TEXT = {"stun": "определён через STUN", "admin": "задан администратором", "interface": "на интерфейсе сервера",
               "previous": "последний известный", "configured": "по настройкам (последнее определение через STUN)"}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_RFC1918 = [ipaddress.ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
_COOKIE = 0x2112A442


def classify(ip: str) -> str:
    """public | private | cgnat | loopback | link-local | reserved | invalid. Документационные диапазоны (192.0.2.0/24 и др.) считаются
    публичными: на них построены примеры и испытания, на настоящих серверах их не бывает."""
    try:
        a = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return "invalid"
    if a.version != 4:
        return "invalid"          # объявляется только IPv4 (как и раньше)
    if a.is_loopback:
        return "loopback"
    if a.is_link_local:
        return "link-local"
    if a in _CGNAT:
        return "cgnat"
    if any(a in n for n in _RFC1918):
        return "private"
    if a.is_multicast or a.is_unspecified or a.is_reserved:
        return "reserved"
    return "public"


def stun_servers(env: dict[str, str]) -> list[tuple[str, int]]:
    out = []
    for s in (env.get("LIVEKIT_STUN_SERVERS") or DEFAULT_STUN).split(","):
        host, _, port = s.strip().rpartition(":")
        if host and port.isdigit():
            out.append((host, int(port)))
    return out


def stun_query(host: str, port: int, timeout: float = 2.0, sock: socket.socket | None = None) -> tuple[str, int] | None:
    """Запрос STUN Binding (RFC 5389): внешний адрес и порт, с которых сервер STUN видит этот запрос; None — нет ответа."""
    try:
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    except OSError:
        return None
    own = sock is None
    s = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        tid = secrets.token_bytes(12)
        s.sendto(struct.pack("!HHI", 0x0001, 0, _COOKIE) + tid, addr)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            data, _ = s.recvfrom(2048)
            if len(data) < 20 or data[8:20] != tid or struct.unpack("!H", data[:2])[0] != 0x0101:
                continue
            return _mapped(data)
    except OSError:
        return None
    finally:
        if own:
            s.close()
    return None


def _mapped(msg: bytes) -> tuple[str, int] | None:
    i, plain = 20, None
    while i + 4 <= len(msg):
        t, ln = struct.unpack("!HH", msg[i:i + 4])
        v = msg[i + 4:i + 4 + ln]
        if len(v) >= 8 and v[1] == 1:
            if t == 0x0020:          # XOR-MAPPED-ADDRESS
                return socket.inet_ntoa(struct.pack("!I", struct.unpack("!I", v[4:8])[0] ^ _COOKIE)), struct.unpack("!H", v[2:4])[0] ^ (_COOKIE >> 16)
            if t == 0x0001:          # MAPPED-ADDRESS (старые серверы)
                plain = socket.inet_ntoa(v[4:8]), struct.unpack("!H", v[2:4])[0]
        i += 4 + ln + (-ln % 4)
    return plain


def probe_public(env: dict[str, str], timeout: float = 2.0) -> dict[str, Any]:
    """Публичный адрес через STUN (до двух ответивших серверов, с одного сокета). `ip` — None, если никто не ответил;
    `consistent` — серверы видят один адрес; `symmetric` — один локальный порт виден с разными внешними портами (симметричный NAT)."""
    res: list[dict[str, Any]] = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", 0))
        for host, port in stun_servers(env):
            m = stun_query(host, port, timeout, s)
            res.append({"server": f"{host}:{port}", "ip": m[0] if m else None, "port": m[1] if m else None})
            if sum(1 for r in res if r["ip"]) >= 2:
                break
    finally:
        s.close()
    ok = [r for r in res if r["ip"]]
    return {"ip": ok[0]["ip"] if ok else None, "results": res, "consistent": len({r["ip"] for r in ok}) <= 1,
            "symmetric": len({r["port"] for r in ok}) > 1}


def trace(dst: str, max_hops: int = 5, timeout: float = 1.0) -> list[str | None]:
    """Адреса первых узлов по пути к dst (как tracepath, без root: UDP с малым TTL и IP_RECVERR). None — узел не ответил."""
    hops: list[str | None] = []
    ip_recverr, msg_errqueue = 11, 0x2000
    for ttl in range(1, max_hops + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            s.setsockopt(socket.IPPROTO_IP, ip_recverr, 1)
            s.sendto(b"peregovorka-trace", (dst, 33434 + ttl))
            hop, end = None, time.monotonic() + timeout
            while time.monotonic() < end and hop is None:
                try:
                    _, anc, _, _ = s.recvmsg(512, 512, socket.MSG_DONTWAIT | msg_errqueue)
                except BlockingIOError:
                    time.sleep(0.02)
                    continue
                for level, typ, data in anc:
                    if level == socket.IPPROTO_IP and typ == ip_recverr and len(data) >= 24 and data[4] == 2:   # SO_EE_ORIGIN_ICMP
                        hop = socket.inet_ntoa(data[20:24])
            hops.append(hop)
            if hop == dst:
                break
        except OSError:
            hops.append(None)
        finally:
            s.close()
    return hops


def nat_layers(hops: list[str | None], skip: int = 0) -> dict[str, Any]:
    """По адресам узлов до первого публичного: `cgnat` — встречен 100.64.0.0/10 (NAT оператора); `private_hops` — сколько частных узлов
    (2 и больше на хосте — вероятен двойной NAT). `skip` — сколько первых узлов не считать (шлюз сети Docker при проверке из контейнера)."""
    seen: list[str] = []
    for h in hops[skip:]:
        if h is None:
            continue
        c = classify(h)
        if c == "public":
            break
        seen.append(h)
    return {"cgnat": any(classify(h) == "cgnat" for h in seen), "private_hops": sum(1 for h in seen if classify(h) == "private"), "hops": seen}


def _ip_or_none(v: str) -> str | None:
    v = (v or "").strip()
    return v if classify(v) != "invalid" else None


def plan(env: dict[str, str], probe: dict[str, Any] | None = None) -> dict[str, Any]:
    """Решение: режим, производные ключи .env (`env`), какие адреса получат клиенты, правила проброса, предупреждения и действия.
    probe — результат probe_public() (None — STUN не опрашивался)."""
    lan = (env.get("LIVEKIT_NODE_IP") or "").strip()
    setting = (env.get("LIVEKIT_PUBLIC_IP") or "auto").strip().lower()
    tcp, udp = (env.get("LIVEKIT_TCP_PORT") or "").strip(), (env.get("LIVEKIT_UDP_PORT") or "").strip()
    prev = _ip_or_none(env.get("LIVEKIT_RTC_NODE_IP", ""))
    warnings: list[str] = []
    actions: list[str] = []
    public, source = None, "none"
    lan_cls = classify(lan)
    if lan_cls == "invalid":
        warnings.append(f"LIVEKIT_NODE_IP ({lan or 'не задан'}) — не IPv4-адрес сервера: медиасоединения настроить нельзя.")
        actions.append("Укажите в LIVEKIT_NODE_IP IPv4-адрес сервера в его сети и выполните scripts/medianet.sh apply.")
    elif lan_cls == "cgnat":
        warnings.append(f"У сервера адрес из диапазона CGNAT ({lan}, 100.64.0.0/10): входящие соединения из интернета через оператора "
                        "обычно невозможны.")
        actions.append("Для пользователей из интернета нужен публичный адрес у сервера или маршрутизатора (услуга оператора), VPN "
                       "или размещение сервера на площадке с публичным адресом.")

    if setting not in ("off", "auto"):
        if classify(setting) == "invalid":
            warnings.append(f"LIVEKIT_PUBLIC_IP={setting}: ожидается auto, off или IPv4-адрес — значение не применено, используется auto.")
            setting = "auto"
        else:
            public, source = setting, "admin"
            if classify(setting) != "public":
                warnings.append(f"LIVEKIT_PUBLIC_IP={setting} — не публичный адрес ({classify(setting)}): из интернета он недоступен.")
    if setting not in ("off",) and public is None and lan_cls == "public":
        public, source = lan, "interface"
    if setting == "auto" and public is None and lan_cls != "invalid":
        got = (probe or {}).get("ip")
        if got and classify(got) == "public":
            public, source = got, "stun"
            if probe and not probe.get("consistent", True):
                warnings.append("Серверы STUN видят разные публичные адреса (несколько выходов в интернет или балансировка): объявляется "
                                f"{got}. Если внешние пользователи не подключаются — задайте адрес явно: LIVEKIT_PUBLIC_IP=<адрес>.")
        elif got:
            warnings.append(f"STUN вернул непубличный адрес {got} ({classify(got)}): вероятен NAT оператора (CGNAT) — проброс портов "
                            "на своём маршрутизаторе не поможет.")
            actions.append("Нужен публичный адрес (услуга оператора) или размещение сервера на площадке с публичным адресом; "
                           "известный публичный адрес можно задать: LIVEKIT_PUBLIC_IP=<адрес>.")
        elif prev and classify(prev) == "public" and prev != lan:
            public, source = prev, ("previous" if probe is not None else "configured")
            if probe is not None:
                warnings.append(f"STUN сейчас не отвечает: оставлен последний известный публичный адрес {prev}. LiveKit при запуске "
                                "проверит STUN сам и при недоступности объявит этот адрес.")
        elif probe is not None:
            warnings.append("Публичный адрес не определён: серверы STUN не ответили (" + ", ".join(
                r["server"] for r in (probe.get("results") or [])) + "). Клиенты из интернета подключиться не смогут.")
            actions.append("Разрешите серверу исходящий UDP к STUN (LIVEKIT_STUN_SERVERS) или задайте публичный адрес явно: "
                           "LIVEKIT_PUBLIC_IP=<адрес>; затем scripts/medianet.sh apply.")

    if setting == "off" or public is None or lan_cls == "invalid":
        mode = "lan"
    elif public == lan:
        mode = "direct"
    else:
        mode = "nat-manual" if source == "admin" else "nat-stun"
    if mode == "lan" and setting == "off":
        actions.append("Режим «только локальная сеть» (LIVEKIT_PUBLIC_IP=off): пользователи из интернета не подключатся — так задумано.")

    node = public if mode != "lan" else lan
    derived = {
        "LIVEKIT_MEDIA_MODE": mode,
        "LIVEKIT_RTC_NODE_IP": node or "",
        "LIVEKIT_RTC_USE_EXTERNAL_IP": "true" if mode == "nat-stun" else "false",
        "LIVEKIT_RTC_ADVERTISE_INTERNAL": "true" if mode in ("nat-stun", "nat-manual") else "false",
        "LIVEKIT_RTC_LAN_IP": lan if mode in ("nat-stun", "nat-manual") else "",
    }
    advertised = [x for x in ([lan, public] if mode.startswith("nat") else [node]) if x]
    forward: list[dict[str, str]] = []
    if mode.startswith("nat"):
        for proto, port, why in (("UDP", udp, "звук и видео (основной путь)"), ("TCP", tcp, "звук и видео, когда у клиента закрыт UDP")):
            if port:
                forward.append({"proto": proto, "external": f"{public}:{port}", "target": f"{lan}:{port}", "why": why})
        forward.append({"proto": "TCP", "external": f"{public}:443", "target": "обратный прокси (HTTPS)", "why": "веб-интерфейс и сигнальное соединение"})
        actions.append("На маршрутизаторе перед сервером пробросьте порты без смены номера и протокола: " + "; ".join(
            f"{f['proto']} {f['external']} → {f['target']}" for f in forward[:2]) + ".")
    return {"mode": mode, "mode_text": MODE_TEXT[mode], "lan_ip": lan, "public_ip": public, "public_source": source, "setting": setting,
            "env": derived, "advertised": advertised, "ports": {"tcp": tcp, "udp": udp}, "forward": forward,
            "warnings": warnings, "actions": actions}


def stale(env: dict[str, str], p: dict[str, Any]) -> list[str]:
    """Какие производные ключи в env расходятся с планом (настройки устарели — нужен scripts/medianet.sh apply)."""
    return [k for k, v in p["env"].items() if (env.get(k) or "").strip() != v and not (k == "LIVEKIT_RTC_NODE_IP" and p["mode"] == "nat-stun")]


def check(cid: str, title: str, status: str, detail: str, action: str = "") -> dict[str, str]:
    return {"id": cid, "title": title, "status": status, "detail": detail, "action": action}


def diagnose(env: dict[str, str], *, probe: dict[str, Any] | None, hops: list[str | None] | None = None, hop_skip: int = 0,
             hairpin: dict[str, Any] | None = None, clients: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Проверки, общие для веба и терминала. hairpin — {"ok": bool, "ms"|"error"}: TCP-соединение с публичным адресом и портом медиа;
    clients — последние пути ICE настоящих клиентов ({"candidate": "udp/host→host", "remote": "udp 192.0.2.2:17882"})."""
    p = plan(env, probe)
    out: list[dict[str, str]] = []
    configured = (env.get("LIVEKIT_MEDIA_MODE") or "").strip()
    st = stale(env, p) if configured else list(DERIVED)
    if st:
        out.append(check("config", "Настройки LiveKit соответствуют сети", "warn",
                         "Настройки медиасоединений не совпадают с текущим определением сети (" + ", ".join(st) + ").",
                         "Выполните scripts/medianet.sh apply — настройки будут пересчитаны, LiveKit перезапущен."))
    else:
        out.append(check("config", "Настройки LiveKit соответствуют сети", "ok", f"Режим: {p['mode_text']}."))
    if p["mode"] == "lan":
        out.append(check("public", "Публичный адрес", "warn" if (env.get("LIVEKIT_PUBLIC_IP") or "auto").lower() != "off" else "ok",
                         "Не используется: клиенты получают только адрес локальной сети " + (p["lan_ip"] or "—") + ".", " ".join(p["actions"])))
    else:
        now = (probe or {}).get("ip")
        src = SOURCE_TEXT.get(p["public_source"], p["public_source"])
        if probe is None:
            out.append(check("public", "Публичный адрес", "ok", f"{p['public_ip']} — {src}. Проверка STUN сейчас не выполнялась."))
        elif now and p["public_ip"] and now != p["public_ip"] and p["mode"] != "direct":
            out.append(check("public", "Публичный адрес", "warn", f"STUN сейчас видит {now}, а клиентам объявляется {p['public_ip']}.",
                             "Если адрес сменился — выполните scripts/medianet.sh apply (в режиме nat-stun LiveKit возьмёт новый адрес при перезапуске)."))
        elif not now and p["mode"] == "nat-stun":
            out.append(check("public", "Публичный адрес", "warn", "STUN сейчас не отвечает; при перезапуске LiveKit объявит последний известный адрес "
                             f"{p['public_ip']}.", "Разрешите исходящий UDP к STUN или задайте LIVEKIT_PUBLIC_IP явно."))
        else:
            out.append(check("public", "Публичный адрес", "ok", f"{p['public_ip']} — {src}."))
    if hops is not None and p["mode"] == "direct":
        out.append(check("nat", "Слои NAT по пути в интернет", "ok", "Не требуется: публичный адрес на интерфейсе сервера."))
    elif hops is not None:
        nl = nat_layers(hops, hop_skip)
        if nl["cgnat"]:
            out.append(check("nat", "Слои NAT по пути в интернет", "fail", "На пути есть адрес оператора из 100.64.0.0/10 (" + ", ".join(nl["hops"]) +
                             "): вероятен CGNAT — входящие соединения из интернета до сервера не дойдут, даже если порты проброшены на своём маршрутизаторе.",
                             "Закажите у оператора публичный адрес (или проброс), используйте VPN или разместите сервер на площадке с публичным адресом."))
        elif nl["private_hops"] >= 2:
            out.append(check("nat", "Слои NAT по пути в интернет", "warn", "Два и более частных узла до интернета (" + ", ".join(nl["hops"]) +
                             "): возможен двойной NAT. Частные адреса бывают и у маршрутизаторов провайдера без NAT — если пользователи из интернета "
                             "подключаются, предупреждение можно не учитывать.",
                             "Если не подключаются — пробросьте порты на каждом маршрутизаторе по цепочке (без смены номера) или переведите внешний в режим моста."))
        else:
            out.append(check("nat", "Слои NAT по пути в интернет", "ok", "Признаков CGNAT и двойного NAT нет" + (f" (узлы: {', '.join(nl['hops'])})" if nl["hops"] else "") + "."))
    if p["mode"].startswith("nat") or p["mode"] == "direct":
        port = p["ports"]["tcp"]
        if hairpin is None:
            pass
        elif hairpin.get("ok"):
            out.append(check("external", "Доступность медиапорта по публичному адресу", "ok",
                             f"TCP {p['public_ip']}:{port} принимает соединения (проверено с сервера через публичный адрес)."))
        elif p["mode"] == "direct":
            out.append(check("external", "Доступность медиапорта по публичному адресу", "fail",
                             f"Соединение с {p['public_ip']}:{port} не установилось ({hairpin.get('error', 'нет ответа')}): LiveKit не принимает "
                             "соединения на адресе сервера.", "Проверьте, что контейнер livekit запущен и порты опубликованы: scripts/medianet.sh check."))
        else:
            out.append(check("external", "Доступность медиапорта по публичному адресу", "unknown",
                             f"С сервера соединение с {p['public_ip']}:{port} не установилось ({hairpin.get('error', 'нет ответа')}). Многие маршрутизаторы "
                             "не пропускают такое «петлевое» соединение, поэтому это не доказывает, что порт закрыт.",
                             "Проверьте проброс: " + "; ".join(f"{f['proto']} {f['external']} → {f['target']}" for f in p["forward"][:2]) +
                             ". Затем войдите в звонок с устройства вне сети организации (мобильная сеть) — ниже появится его путь."))
    if clients is not None:
        out.append(_clients_check(clients, p))
    return {"plan": p, "checks": out}


def _clients_check(clients: list[dict[str, Any]], p: dict[str, Any]) -> dict[str, str]:
    seen: dict[str, int] = {}
    for c in clients:
        r = str(c.get("remote") or "").strip()
        if r:
            seen[r] = seen.get(r, 0) + 1
    if not seen:
        return check("clients", "Адреса ICE у настоящих клиентов", "unknown", "Пока нет данных: клиенты новой версии сообщают адрес сервера, через который идёт звук.",
                     "Войдите в звонок с компьютера в офисе и с устройства вне сети организации.")
    foreign = [r for r in seen if r.split(" ")[-1].rsplit(":", 1)[0] not in p["advertised"]]
    detail = "; ".join(f"{r} — {n}" for r, n in sorted(seen.items(), key=lambda x: -x[1])[:6])
    if foreign:
        return check("clients", "Адреса ICE у настоящих клиентов", "warn", f"{detail}. Не из объявляемых ({', '.join(p['advertised'])}): {', '.join(foreign)}.",
                     "Выполните scripts/medianet.sh apply и перезапустите звонки.")
    tcp_only = all(r.startswith("tcp ") for r in seen)
    return check("clients", "Адреса ICE у настоящих клиентов", "warn" if tcp_only else "ok",
                 detail + (". Все через TCP: проверьте проброс и межсетевой экран для UDP." if tcp_only else "."))


# --- .env

def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def set_env_keys(text: str, values: dict[str, str]) -> str:
    """Заменяет значения ключей (первое вхождение) или дописывает их в конец; остальной файл не меняется."""
    lines, done = text.splitlines(), set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if k in values and k not in done:
            lines[i] = f"{k}={values[k]}"
            done.add(k)
    rest = [f"{k}={v}" for k, v in values.items() if k not in done]
    if rest:
        lines += ["", "# Медиасоединения LiveKit — вычисляет scripts/medianet.py (установка, обновление, scripts/medianet.sh apply)"] + rest
    return "\n".join(lines) + "\n"


INPUTS = ("LIVEKIT_NODE_IP", "LIVEKIT_PUBLIC_IP", "LIVEKIT_STUN_SERVERS", "LIVEKIT_TCP_PORT", "LIVEKIT_UDP_PORT") + DERIVED


def env_from_process() -> dict[str, str]:
    """Входные и производные ключи из окружения контейнера backend (порты там называются LIVEKIT_RTC_TCP_PORT/UDP_PORT); секретов нет."""
    env = {k: os.environ.get(k, "") for k in INPUTS}
    env["LIVEKIT_TCP_PORT"] = env["LIVEKIT_TCP_PORT"] or os.environ.get("LIVEKIT_RTC_TCP_PORT", "")
    env["LIVEKIT_UDP_PORT"] = env["LIVEKIT_UDP_PORT"] or os.environ.get("LIVEKIT_RTC_UDP_PORT", "")
    return env
