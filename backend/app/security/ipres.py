"""Определение адреса клиента.

Принципы (OWASP: HTTP-заголовки с адресом клиента подделываются; их можно использовать только от явно доверенного узла):
  * Адрес TCP-соединения — единственное, чему верим безусловно. Заголовки (`X-Forwarded-For`, `X-Real-IP`, `Forwarded`, заголовок WAF/CDN) принимаются, только если соединение
    пришло от узла из списка «откуда принимаются заголовки» (по умолчанию — частные и локальные адреса: сеть контейнеров, host-nginx на этой же машине).
  * Контейнер web сообщает backend, с какого адреса подключился к нему САМ клиент или внешний прокси (`X-PG-Peer`; заголовок web всегда перезаписывает, подделать его снаружи нельзя).
    Если этот узел не входит в доверенные прокси (и не частный), заголовки о клиенте игнорируются: так клиент, который подключился к серверу напрямую, не может подставить чужой адрес.
  * В цепочке `X-Forwarded-For` идём справа налево, пропуская доверенные прокси (адреса/подсети из настройки) и ещё `proxy_hops` прокси, не описанных сетями; первый оставшийся адрес — клиент.
    Левая часть цепочки (её пишет клиент) не используется.
  * IPv4-mapped IPv6 приводится к IPv4; для счётчиков IPv6 объединяется по /64.
Разрешение — синхронное и без обращений к базе: настройки лежат в `app.state.netcfg`, их обновляет фоновая задача и запись в админке.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

PEER_HEADER = "x-pg-peer"          # его выставляет контейнер web (nginx.conf); backend без web его не получит

_PRIVATE_DEFAULT = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10"]
_TOKEN = re.compile(r"^[0-9A-Fa-f:.\[\]]{2,48}$")


def norm_ip(raw: str | None) -> str | None:
    """Строка → нормализованный адрес (IPv4-mapped → IPv4), иначе None. Порт и квадратные скобки отбрасываются."""
    s = (raw or "").strip().strip('"')
    if not s or len(s) > 64:
        return None
    if s.startswith("["):
        s = s[1:].split("]", 1)[0]
    elif s.count(":") == 1 and "." in s:
        s = s.split(":", 1)[0]               # IPv4:порт
    try:
        a = ipaddress.ip_address(s.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return str(a)


def ip_key(ip: str) -> str:
    """Ключ счётчиков: IPv4 как есть, IPv6 — по префиксу /64 (иначе перебор меняет адрес в пределах своей сети и обходит лимиты)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(a, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{a}/64", strict=False).network_address) + "/64"
    return str(a)


def parse_nets(text: str | None) -> list:
    out = []
    for item in re.split(r"[,\s;]+", text or ""):
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue                      # некорректная запись не должна ронять запросы (её отвергает проверка при сохранении)
    return out


def in_nets(ip: str | None, nets: list) -> bool:
    if not ip:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in nets if n.version == a.version)


@dataclass(frozen=True)
class NetCfg:
    mode: str = "auto"
    header_name: str = ""
    trusted: tuple = ()
    hops: int = 1
    accept_from: tuple = ()

    @classmethod
    def build(cls, group, env_cidrs: str, env_hops: int) -> "NetCfg":
        """group — `NetTrustSettings` (или None); пустые поля берут значения из .env (TRUSTED_PROXY_CIDRS, TRUSTED_PROXY_HOPS)."""
        trusted = parse_nets(getattr(group, "trusted_proxies", "") or env_cidrs)
        accept = parse_nets(getattr(group, "accept_from", "")) or parse_nets(",".join(_PRIVATE_DEFAULT))
        hops = getattr(group, "proxy_hops", None)
        return cls(getattr(group, "mode", "auto"), (getattr(group, "header_name", "") or "").lower(), tuple(trusted), env_hops if hops is None else hops, tuple(accept))


@dataclass
class IpResolution:
    ip: str
    source: str                          # откуда взят адрес: tcp | x-pg-peer | x-forwarded-for | x-real-ip | forwarded | header
    tcp_peer: str | None
    web_peer: str | None                 # кто подключился к web (по X-PG-Peer)
    chain: list[str] = field(default_factory=list)
    headers_accepted: bool = False
    notes: list[str] = field(default_factory=list)


def _chain(xff: str) -> list[str]:
    out = []
    for p in (x.strip() for x in (xff or "").split(",")):
        a = norm_ip(p) if p and _TOKEN.match(p.strip('"')) else None
        if a:
            out.append(a)
    return out


def _forwarded_chain(value: str) -> list[str]:
    """`Forwarded: for=192.0.2.60;proto=http, for="[2001:db8::1]:4711"` → список адресов `for` в порядке следования."""
    out = []
    for elem in (value or "").split(","):
        for part in elem.split(";"):
            k, _, v = part.strip().partition("=")
            if k.lower() == "for":
                a = norm_ip(v)
                if a:
                    out.append(a)
    return out


def _walk(chain: list[str], trusted: list, hops: int) -> str | None:
    extra = max(0, hops)
    for i in range(len(chain) - 1, -1, -1):
        if in_nets(chain[i], trusted):
            continue
        if extra > 0:
            extra -= 1
            continue
        return chain[i]
    return chain[0] if chain else None       # вся цепочка из доверенных прокси — самый левый адрес


def resolve(headers, tcp_peer: str | None, cfg: NetCfg) -> IpResolution:
    """headers — любой объект с .get(name) (регистр не важен), tcp_peer — адрес TCP-соединения с backend."""
    tcp = norm_ip(tcp_peer) or (tcp_peer or "unknown")
    h = lambda n: headers.get(n, "") or ""      # noqa: E731
    web_peer = norm_ip(h(PEER_HEADER))
    res = IpResolution(tcp, "tcp", tcp, web_peer)
    if cfg.mode == "peer":
        res.notes.append("режим «адрес соединения»: заголовки не используются")
        if web_peer and in_nets(tcp, list(cfg.accept_from)):
            res.ip, res.source = web_peer, "x-pg-peer"
        return res
    # 1. можно ли вообще верить заголовкам: соединение с backend — из сети контейнеров/доверенных адресов
    if not in_nets(tcp, list(cfg.accept_from)) and not in_nets(tcp, list(cfg.trusted)):
        res.notes.append("соединение с backend пришло не из сети контейнеров и не от доверенного прокси: заголовки адреса игнорируются (прямое обращение к backend)")
        return res
    # 2. узел, подключившийся к web (клиент или внешний прокси)
    edge = web_peer or tcp
    trusted = list(cfg.trusted)
    edge_trusted = in_nets(edge, trusted) or (cfg.mode == "auto" and in_nets(edge, list(cfg.accept_from)))
    if web_peer:
        res.ip, res.source = web_peer, "x-pg-peer"
        if not edge_trusted:
            res.notes.append(f"к web подключился {web_peer}: он не входит в доверенные прокси — это клиент, заголовки адреса от него игнорируются")
            return res
    elif not in_nets(tcp, trusted) and cfg.mode != "auto":
        res.notes.append("нет X-PG-Peer и соединение не от доверенного прокси: заголовки игнорируются")
        return res
    res.headers_accepted = True
    mode = cfg.mode
    chain: list[str] = []
    picked: str | None = None
    if mode in ("auto", "xff"):
        chain = _chain(h("x-forwarded-for"))
        res.chain = chain
        picked = _walk(chain, trusted, cfg.hops - 1 if mode == "auto" and cfg.hops > 0 else cfg.hops) if chain else None
        src = "x-forwarded-for"
        if picked is None and mode == "auto":
            picked, src = norm_ip(h("x-real-ip")), "x-real-ip"
    elif mode == "x_real_ip":
        picked, src = norm_ip(h("x-real-ip")), "x-real-ip"
    elif mode == "forwarded":
        chain = _forwarded_chain(h("forwarded"))
        res.chain = chain
        picked, src = (_walk(chain, trusted, cfg.hops) if chain else None), "forwarded"
    else:                                   # header
        picked, src = norm_ip(h(cfg.header_name)) if cfg.header_name else None, "header"
    if picked:
        res.ip, res.source = picked, src
    elif web_peer is None:
        res.ip, res.source = tcp, "tcp"
        res.notes.append("заголовок с адресом клиента не пришёл: используется адрес соединения")
    return res
