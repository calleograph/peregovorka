"""Защита от SSRF для адресов webhook: что можно вызывать с сервера и как это проверяется.

Правила:
* схема только `https` (`http` — по явному разрешению администратора для изолированных сетей); адрес без логина/пароля;
* имя узла разрешается в IP **всех** семейств, и **каждый** адрес должен быть допустим (иначе подмена одного A-записью на внутренний адрес обходила бы проверку);
* по умолчанию запрещены: loopback, частные сети (RFC 1918, ULA), CGNAT, link-local, multicast, зарезервированные, «неопределённые» адреса;
* **никогда** не разрешаются адреса метаданных облака (169.254.169.254 и аналоги) — даже если администратор разрешил соседнюю сеть;
* on-prem политика администратора: список узлов и сетей, куда внутренние адреса всё же разрешены (например, `crm.corp.local, 10.20.0.0/16`). Запись должна быть явной;
* проверка повторяется **при каждой отправке** (защита от смены DNS, «DNS rebinding»), а соединение идёт на проверенный IP с прежним именем в Host и SNI.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

HARD_BLOCKED = [ipaddress.ip_network(n) for n in ("169.254.169.254/32", "fd00:ec2::254/128", "100.100.100.200/32")]
LINK_LOCAL_AND_SPECIAL = [ipaddress.ip_network(n) for n in ("169.254.0.0/16", "fe80::/10", "0.0.0.0/8", "224.0.0.0/4", "ff00::/8", "240.0.0.0/4", "::/128")]
INTERNAL = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "100.64.0.0/10")]


class UrlRejected(ValueError):
    """Адрес не допускается политикой (текст — для администратора, без внутренних подробностей сети)."""


def parse_allow(text: str) -> tuple[list, set[str]]:
    """«crm.corp.local, 10.20.0.0/16» → (сети, имена узлов). Нераспознанные записи — ошибка сохранения, а не молчаливый пропуск."""
    nets: list = []
    hosts: set[str] = set()
    for raw in (text or "").replace(";", ",").replace("\n", ",").split(","):
        item = raw.strip().lower()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            if not all(ch.isalnum() or ch in ".-_" for ch in item) or item.startswith((".", "-")) or ".." in item:
                raise ValueError(f"Недопустимая запись политики: «{item[:60]}». Укажите имя узла или сеть вида 10.20.0.0/16") from None
            hosts.add(item)
    return nets, hosts


def ip_problem(ip: ipaddress._BaseAddress, allow_nets: list, host_allowed: bool) -> str | None:
    """Причина, по которой адрес запрещён, либо None."""
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped                                   # ::ffff:10.0.0.1 проверяем как 10.0.0.1
    if any(ip in n for n in HARD_BLOCKED):
        return "адрес служебных метаданных облака запрещён всегда"
    explicit = host_allowed or any(ip in n for n in allow_nets)
    if any(ip in n for n in LINK_LOCAL_AND_SPECIAL) and not any(ip in n for n in allow_nets):
        return "адреса link-local, multicast и зарезервированные запрещены (их можно разрешить только явной сетью в политике)"
    if any(ip in n for n in INTERNAL) and not explicit:
        return "внутренние адреса (loopback, частные сети) запрещены политикой; администратор может добавить узел или сеть в разрешённые"
    return None


async def default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    out: list[str] = []
    for fam, _t, _p, _c, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in out:
            out.append(ip)
    return out


async def validate_url(url: str, *, allow_http: bool = False, allow_text: str = "", resolver=default_resolver) -> tuple[str, int, str, list[str]]:
    """Проверка адреса. Возвращает (узел, порт, схема, проверенные IP). Бросает UrlRejected с понятной причиной."""
    if not isinstance(url, str) or len(url) > 500 or any(ord(c) < 33 for c in url):
        raise UrlRejected("Адрес пуст, слишком длинный или содержит пробелы и управляющие символы")
    p = urlsplit(url)
    if p.scheme not in ("https", "http"):
        raise UrlRejected("Адрес должен начинаться с https://")
    if p.scheme == "http" and not allow_http:
        raise UrlRejected("Допускается только https:// (http:// разрешает администратор в настройках для изолированных сетей)")
    if p.username is not None or p.password is not None:
        raise UrlRejected("Логин и пароль в адресе не допускаются: для подлинности используется подпись события")
    if p.fragment:
        raise UrlRejected("Фрагмент (#…) в адресе не допускается")
    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlRejected("В адресе нет имени узла")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        raise UrlRejected("Некорректный порт в адресе") from None
    try:
        allow_nets, allow_hosts = parse_allow(allow_text)
    except ValueError as exc:
        raise UrlRejected(str(exc)) from None
    host_allowed = host in allow_hosts
    try:
        literal = ipaddress.ip_address(host)
        candidates = [str(literal)]
    except ValueError:
        try:
            candidates = await resolver(host, port)
        except (OSError, UnicodeError):
            raise UrlRejected("Имя узла не разрешается (DNS): проверьте адрес") from None
    if not candidates:
        raise UrlRejected("Имя узла не разрешается (DNS): проверьте адрес")
    for c in candidates:
        why = ip_problem(ipaddress.ip_address(c), allow_nets, host_allowed)
        if why:
            raise UrlRejected(f"{why}")
    return host, port, p.scheme, candidates


def pinned_url(url: str, ip: str) -> str:
    """Тот же адрес, но с проверенным IP вместо имени (соединение идёт именно туда; имя остаётся в Host и SNI)."""
    p = urlsplit(url)
    netloc = f"[{ip}]" if ":" in ip else ip
    if p.port:
        netloc += f":{p.port}"
    return p._replace(netloc=netloc).geturl()
