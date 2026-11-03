#!/usr/bin/env python3
"""medianet.py — адреса медиасоединений LiveKit на сервере (решение — backend/app/services/medianet.py, тот же код, что у веб-раздела).

  python3 -I scripts/medianet.py configure --env .env [--write] [--public-ip auto|off|IPv4] [--offline]
      определить адрес сервера в его сети, публичный адрес (STUN с сервера) и режим; --write — записать в .env производные ключи.
      Явные настройки администратора (LIVEKIT_PUBLIC_IP=<адрес>|off, LIVEKIT_STUN_SERVERS, порты) сохраняются.
      Прежняя схема (.env без LIVEKIT_PUBLIC_IP, внешний адрес в LIVEKIT_NODE_IP) переносится: адрес → LIVEKIT_PUBLIC_IP, в LIVEKIT_NODE_IP —
      адрес интерфейса. Адрес, которого нет на интерфейсах, иначе заменяется адресом маршрута по умолчанию.
  python3 -I scripts/medianet.py check --env .env [--json] — проверки: публичный адрес, слои NAT (CGNAT, двойной NAT), адрес на
      интерфейсе, порты (прослушивание и публикация Docker — номера внутри и снаружи совпадают), межсетевой экран (с root), что LiveKit
      фактически объявляет клиентам (его журнал), доступность медиапорта по публичному адресу. Код 0 — без ошибок, 1 — есть ошибки.
Применение с перезапуском LiveKit — scripts/medianet.sh apply.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("medianet", os.path.join(ROOT, "backend", "app", "services", "medianet.py"))
mn = importlib.util.module_from_spec(_spec)
sys.modules["medianet"] = mn
_spec.loader.exec_module(mn)


def sh(*cmd: str, timeout: float = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def local_ipv4() -> tuple[list[str], str]:
    """Адреса IPv4 интерфейсов и адрес-источник маршрута по умолчанию (как у установщика)."""
    rc, out = sh("ip", "-o", "-4", "addr", "show")
    ips = re.findall(r"\sinet\s(\d+\.\d+\.\d+\.\d+)/", out) if rc == 0 else []
    rc, out = sh("ip", "route", "get", "1.1.1.1")
    m = re.search(r"\bsrc\s(\d+\.\d+\.\d+\.\d+)", out) if rc == 0 else None
    return ips, (m.group(1) if m else "")


def fix_lan(env: dict[str, str], legacy: bool) -> tuple[dict[str, str], list[str]]:
    """LIVEKIT_NODE_IP должен быть адресом интерфейса сервера. Возвращает изменения .env и сообщения.
    legacy — .env прежней схемы (без LIVEKIT_PUBLIC_IP): внешний адрес из LIVEKIT_NODE_IP переносится в LIVEKIT_PUBLIC_IP."""
    ips, src = local_ipv4()
    if not ips:
        return {}, ["адреса интерфейсов не прочитаны (нет команды ip): LIVEKIT_NODE_IP не проверялся"]
    cur = (env.get("LIVEKIT_NODE_IP") or "").strip()
    if cur in ips:
        return {}, []
    if not src:
        return {}, [f"LIVEKIT_NODE_IP={cur or '—'} нет на интерфейсах сервера, а маршрут по умолчанию не найден: оставлено как есть"]
    ch = {"LIVEKIT_NODE_IP": src}
    if legacy and mn.classify(cur) == "public":
        ch["LIVEKIT_PUBLIC_IP"] = cur
        return ch, [f"прежняя схема: в LIVEKIT_NODE_IP был внешний адрес {cur} — перенесён в LIVEKIT_PUBLIC_IP; LIVEKIT_NODE_IP={src} (адрес интерфейса)"]
    return ch, [f"LIVEKIT_NODE_IP={cur or '—'} нет на интерфейсах сервера — заменён на {src} (адрес маршрута по умолчанию)"]


def needs_probe(env: dict[str, str]) -> bool:
    setting = (env.get("LIVEKIT_PUBLIC_IP") or "auto").strip().lower()
    return setting == "auto" and mn.classify(env.get("LIVEKIT_NODE_IP", "")) != "public"


def write_env(path: str, values: dict[str, str]) -> None:
    text = open(path, encoding="utf-8").read()
    new = mn.set_env_keys(text, values)
    if new == text:
        return
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".env.medianet.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    os.chmod(tmp, st.st_mode & 0o7777)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except PermissionError:
        pass
    os.replace(tmp, path)


def cmd_configure(a: argparse.Namespace) -> int:
    env = mn.parse_env(open(a.env, encoding="utf-8").read())
    legacy = not (env.get("LIVEKIT_PUBLIC_IP") or "").strip() and not a.public_ip
    changes: dict[str, str] = {}
    if a.public_ip and a.public_ip.strip().lower() not in ("auto", "off") and mn.classify(a.public_ip) == "invalid":
        print(f"[FAIL] --public-ip {a.public_ip}: нужен auto, off или IPv4-адрес", file=sys.stderr)
        return 2
    if a.public_ip:
        changes["LIVEKIT_PUBLIC_IP"] = a.public_ip.strip().lower() if a.public_ip.strip().lower() in ("auto", "off") else a.public_ip.strip()
    elif not env.get("LIVEKIT_PUBLIC_IP"):
        changes["LIVEKIT_PUBLIC_IP"] = "auto"
    env.update(changes)
    lan_ch, msgs = fix_lan(env, legacy) if not a.offline else ({}, [])
    changes.update(lan_ch)
    env.update(lan_ch)
    probe = mn.probe_public(env) if needs_probe(env) and not a.offline else None
    p = mn.plan(env, probe)
    changes.update(p["env"])
    for m in msgs:
        print(f"[i] {m}")
    print(f"[ok] Медиасоединения: {p['mode_text']} ({p['mode']}); клиентам объявляется: {', '.join(p['advertised']) or '—'}; "
          f"порты UDP {p['ports']['udp'] or '—'}, TCP {p['ports']['tcp'] or '—'}")
    if probe is not None:
        print("[i] STUN: " + "; ".join(f"{r['server']} → {r['ip'] or 'нет ответа'}" for r in probe["results"]))
    for f in p["forward"][:2]:
        print(f"[i] проброс на маршрутизаторе: {f['proto']} {f['external']} → {f['target']} ({f['why']})")
    for w in p["warnings"]:
        print(f"[warn] {w}", file=sys.stderr)
    if a.write:
        write_env(a.env, changes)
        print(f"[ok] Записано в {a.env}: " + ", ".join(f"{k}={v}" for k, v in changes.items()))
    return 0


def _livekit_container(env: dict[str, str]) -> str:
    proj = env.get("COMPOSE_PROJECT_NAME", "")
    if not proj:
        return ""
    rc, out = sh("docker", "ps", "--filter", f"label=com.docker.compose.project={proj}", "--filter", "label=com.docker.compose.service=livekit",
                 "--format", "{{.Names}}")
    return out.strip().splitlines()[0] if rc == 0 and out.strip() else ""


def host_checks(env: dict[str, str], p: dict[str, object]) -> list[dict[str, str]]:
    chk = mn.check
    out: list[dict[str, str]] = []
    ips, _ = local_ipv4()
    lan = p["lan_ip"]
    if ips:
        out.append(chk("lan", "Адрес сервера в его сети (LIVEKIT_NODE_IP)", "ok" if lan in ips else "fail",
                       f"{lan} " + ("есть на интерфейсе сервера." if lan in ips else f"не найден на интерфейсах ({', '.join(ips)})."),
                       "" if lan in ips else "Выполните scripts/medianet.sh apply — адрес будет исправлен."))
    tcp, udp = p["ports"]["tcp"], p["ports"]["udp"]
    c = _livekit_container(env)
    if not c:
        out.append(chk("docker", "Порты LiveKit опубликованы Docker", "fail", "Контейнер livekit этого экземпляра не запущен.",
                       "Запустите службы: scripts/update.sh или docker compose up -d livekit."))
        return out
    rc, ports = sh("docker", "port", c)
    want = [f"{udp}/udp", f"{tcp}/tcp"]
    bad = [w for w in want if not re.search(rf"^{re.escape(w)} -> [^\n]*:{w.split('/')[0]}$", ports, re.M)]
    out.append(chk("docker", "Порты LiveKit опубликованы Docker (номер внутри = снаружи)", "fail" if bad else "ok",
                   ("Не опубликованы или опубликованы с другим номером: " + ", ".join(bad)) if bad else ports.strip().replace("\n", "; "),
                   "Пересоздайте контейнер: scripts/medianet.sh apply." if bad else ""))
    rc, cfg = sh("docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", c)
    m_tcp, m_udp = re.search(r"tcp_port: (\d+)", cfg), re.search(r"udp_port: (\d+)", cfg)
    same = m_tcp and m_udp and m_tcp.group(1) == tcp and m_udp.group(1) == udp
    out.append(chk("rtcports", "Порты в конфигурации LiveKit совпадают с .env", "ok" if same else "fail",
                   f"tcp_port {m_tcp.group(1) if m_tcp else '?'}, udp_port {m_udp.group(1) if m_udp else '?'}; в .env TCP {tcp}, UDP {udp}.",
                   "" if same else "Контейнер запущен со старыми настройками: scripts/medianet.sh apply."))
    rc, listen = sh("ss", "-H", "-lntu")
    if rc == 0:
        lt = re.search(rf"^tcp\s+LISTEN\s.*:{tcp}\s", listen, re.M) is not None
        lu = re.search(rf"^udp\s.*:{udp}\s", listen, re.M) is not None
        out.append(chk("listen", "Порты слушаются на сервере", "ok" if lt and lu else "unknown",
                       f"TCP {tcp}: {'да' if lt else 'не видно'}; UDP {udp}: {'да' if lu else 'не видно'}" +
                       ("" if lt and lu else " (при публикации Docker без userland-proxy порт не виден в ss — смотрите строку про публикацию Docker).")))
    rc, logs = sh("docker", "logs", "--tail", "400", c, timeout=30)
    adv: list[str] = []
    for line in logs.splitlines():
        m = re.search(r"адрес локальной сети (\S+) объявляется", line)
        if m:
            adv.append(m.group(1))
        m = re.search(r"(using explicit node IP|no external IPs found, using node IP|using external IPs).*?(\{.*\})", line)
        if m:
            adv += [x for x in re.findall(r"(\d+\.\d+\.\d+\.\d+)", m.group(2))]
    adv = sorted(set(adv))
    if not adv:
        out.append(chk("ice", "Адреса, которые LiveKit объявляет клиентам (ICE)", "unknown", "В журнале LiveKit нет строки запуска (журнал обрезан?).",
                       "Перезапустите LiveKit (scripts/medianet.sh apply) и повторите проверку."))
    else:
        want_adv = set(p["advertised"])
        ok = want_adv <= set(adv) if p["mode"] != "nat-stun" else p["lan_ip"] in adv and any(mn.classify(x) == "public" for x in adv)
        out.append(chk("ice", "Адреса, которые LiveKit объявляет клиентам (ICE)", "ok" if ok else "fail",
                       f"по журналу LiveKit: {', '.join(adv)}; ожидается: {', '.join(p['advertised'])} (+ адрес контейнера, клиентам не мешает).",
                       "" if ok else "Контейнер запущен со старыми настройками: scripts/medianet.sh apply."))
    if os.geteuid() == 0:
        rc, du = sh("iptables", "-S", "DOCKER-USER")
        drops = [x for x in du.splitlines() if re.search(r"-j (DROP|REJECT)", x)] if rc == 0 else []
        rc2, ufw = sh("ufw", "status")
        note = "ufw активен (порты Docker обходят его правила INPUT)" if rc2 == 0 and "Status: active" in ufw else "ufw не активен"
        out.append(chk("firewall", "Локальный межсетевой экран", "warn" if drops else "ok",
                       (f"В цепочке DOCKER-USER есть запрещающие правила: {'; '.join(drops)}. " if drops else "Запрещающих правил для портов Docker нет; ") + note + ".",
                       f"Убедитесь, что правила DOCKER-USER пропускают UDP {udp} и TCP {tcp}." if drops else ""))
    else:
        out.append(chk("firewall", "Локальный межсетевой экран", "unknown", "Не проверен: нужны права root.", "sudo scripts/medianet.sh check"))
    return out


def cmd_check(a: argparse.Namespace) -> int:
    env = mn.parse_env(open(a.env, encoding="utf-8").read())
    probe = mn.probe_public(env) if (env.get("LIVEKIT_PUBLIC_IP") or "auto").strip().lower() != "off" else None
    hops = None
    for host, _ in mn.stun_servers(env):
        try:
            hops = mn.trace(socket.gethostbyname(host))
            break
        except OSError:
            continue
    p = mn.plan(env, probe)
    hp = None
    if p["mode"] != "lan" and p["public_ip"] and p["ports"]["tcp"]:
        try:
            with socket.create_connection((p["public_ip"], int(p["ports"]["tcp"])), timeout=3):
                hp = {"ok": True}
        except OSError as e:
            hp = {"ok": False, "error": e.strerror or str(e)}
    d = mn.diagnose(env, probe=probe, hops=hops, hairpin=hp)
    if not a.network_only:
        d["checks"] = d["checks"][:1] + host_checks(env, p) + d["checks"][1:]
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        mark = {"ok": "[ok]", "warn": "[warn]", "fail": "[FAIL]", "unknown": "[?]"}
        print(f"Медиасоединения: {p['mode_text']} ({p['mode']})")
        print(f"  адрес в сети сервера: {p['lan_ip'] or '—'}; публичный: {p['public_ip'] or '—'}; клиентам объявляется: {', '.join(p['advertised']) or '—'}")
        if probe is not None:
            print("  STUN: " + "; ".join(f"{r['server']} → {r['ip'] or 'нет ответа'}" for r in probe["results"]))
        for c in d["checks"]:
            print(f"{mark[c['status']]} {c['title']}: {c['detail']}")
            if c["action"]:
                print(f"       → {c['action']}")
        if p["forward"]:
            print("Проброс на маршрутизаторе (номер и протокол не меняются):")
            for f in p["forward"]:
                print(f"  {f['proto']:3} {f['external']:24} → {f['target']}  ({f['why']})")
        for w in p["warnings"]:
            print(f"[warn] {w}")
    return 1 if any(c["status"] == "fail" for c in d["checks"]) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("configure")
    c.add_argument("--env", default=os.path.join(ROOT, ".env"))
    c.add_argument("--write", action="store_true")
    c.add_argument("--public-ip")
    c.add_argument("--offline", action="store_true", help="без STUN и без чтения интерфейсов (подготовка .env на другой машине)")
    k = sub.add_parser("check")
    k.add_argument("--env", default=os.path.join(ROOT, ".env"))
    k.add_argument("--json", action="store_true")
    k.add_argument("--network-only", action="store_true", help="без проверок Docker, портов и межсетевого экрана этого сервера")
    a = ap.parse_args()
    if not os.path.isfile(a.env):
        print(f"[FAIL] нет файла {a.env}", file=sys.stderr)
        return 2
    return cmd_configure(a) if a.cmd == "configure" else cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
