"""Свои узлы CDN Peregovorka: раздача HLS эфиров зрителям с отдельных серверов.

Разделение ролей. Основной сервер формирует эфир (LiveKit, Egress, HLS), авторизует зрителя и выдаёт ему подписанную ссылку на узел.
Узел (nginx + njs, пакет `app/assets/cdn_node`) сам проверяет подпись открытым ключом, забирает сегменты с основного сервера по мере
надобности (origin pull, `/cdn-origin/<доступ>/…` с ключом узла) и раздаёт их из своего кеша всем зрителям.

* Подпись ссылки узла — ES256 строки `pgcdn1|<доступ>|<срок>`; путь `/hls/<доступ>/<срок>/<kid>.<подпись>/<файл>`. Закрытый ключ не хранится:
  он выводится из главного ключа приложения и номера поколения (`live.cdn_key_gen`), поэтому смена ключа — это смена номера. Узлы знают
  открытые ключи текущего и предыдущего поколений; ссылки, подписанные позапрошлым ключом, перестают открываться.
* Срок подписи округляется вверх до окна (как у прямой раздачи): все зрители окна получают один путь, узел проверяет подпись один раз
  и запоминает её до конца срока. Кеш узла не зависит от подписи (ключ — доступ и имя файла).
* Отзыв ссылки зрителя удаляет `acc/<доступ>` на основном сервере: узел перестаёт получать плейлист в течение 1–2 с (кеш плейлиста — 1 с).
* Выбор узла — взвешенное рандеву-хеширование по зрителю среди исправных включённых узлов: зритель остаётся на своём узле, при выпадении
  узла уходят только его зрители. Все узлы недоступны — прямая раздача с основного сервера (если разрешена).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import math
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import or_, select

from ..models import CdnNode
from .live import SECRET_GID, SECRET_MODE

log = logging.getLogger(__name__)

NODE_VERSION = (Path(__file__).resolve().parent.parent / "assets" / "cdn_node" / "VERSION").read_text().strip()
POLL_S = 15                 # опрос узлов
DOWN_AFTER = 2              # подряд неудачных опросов — узел исключается из выдачи
UP_AFTER = 2                # подряд удачных — возвращается
LIVE_KBPS_DEFAULT = 5000    # 1080p — качество новых комнат по умолчанию
_N = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)     # порядок группы P-256


# ------------------------------------------------------------------------------------------------ ключи подписи
def _private_key(master: str, gen: int) -> ec.EllipticCurvePrivateKey:
    seed = hmac.new(master.encode(), f"peregovorka/cdn-es256/v1/{gen}".encode(), hashlib.sha512).digest()
    return ec.derive_private_key(int.from_bytes(seed, "big") % (_N - 1) + 1, ec.SECP256R1())


def _spki(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def kid_of(spki: bytes) -> str:
    return hashlib.sha256(spki).hexdigest()[:8]


def master_of(settings) -> str:
    return settings.app_master_key or settings.livekit_api_secret or ""


def public_keys(master: str, gen: int, *, ahead: bool = False) -> dict[str, str]:
    """Открытые ключи, которые узел принимает: текущее и предыдущее поколение (kid → SPKI base64). ahead — ещё и следующее
    (смена ключа: узлы сначала узнают новый ключ, и только потом им начинают подписывать ссылки)."""
    out = {}
    for g in ((gen + 1, gen) if ahead else (gen, gen - 1)):
        if g >= 1:
            spki = _spki(_private_key(master, g))
            out[kid_of(spki)] = base64.b64encode(spki).decode()
    return out


def sign_path(master: str, gen: int, access: str, ttl_s: int, *, bucket_s: int = 300, now: float | None = None, name: str = "index.m3u8") -> tuple[str, int]:
    """Путь на узле CDN и момент истечения (срок округляется вверх до окна, как у прямой раздачи — один путь на всех зрителей окна)."""
    t = int(now if now is not None else time.time())
    exp = ((t + ttl_s) // bucket_s + 1) * bucket_s
    key = _private_key(master, gen)
    r, s = decode_dss_signature(key.sign(f"pgcdn1|{access}|{exp}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode().rstrip("=")
    return f"/hls/{access}/{exp}/{kid_of(_spki(key))}.{sig}/{name}", exp


# ------------------------------------------------------------------------------------------------ выбор узла
def _score(node_id: str, viewer: str, weight: int) -> float:
    h = int.from_bytes(hashlib.sha256(f"{node_id}|{viewer}".encode()).digest()[:8], "big") / 2**64
    return -max(1, weight) / math.log(max(h, 1e-12))         # взвешенное рандеву-хеширование


def pick(nodes: list[CdnNode], viewer: str, avoid: str | None = None) -> CdnNode | None:
    ok = [n for n in nodes if n.enabled and n.state == "ready" and n.health == "up" and n.public_name != avoid]
    if not ok and avoid:                                     # единственный исправный узел — тот, что плеер просил обойти: всё равно он
        ok = [n for n in nodes if n.enabled and n.state == "ready" and n.health == "up"]
    return max(ok, key=lambda n: _score(str(n.id), viewer, n.weight)) if ok else None


# ------------------------------------------------------------------------------------------------ расчёт оборудования
# Константы — из нагрузочных испытаний (docs/BROADCAST_CDN.md «Испытания»); пересматриваются по новым замерам.
TLS_GBPS_PER_CORE = 1.5      # раздача сегментов по HTTPS на одно ядро (замер: 7,9 Гбит/с — 4,8 ядра, 9,4 Гбит/с — 5,0 ядра; без kTLS)
OVERHEAD = 1.06              # HTTP/TLS/TCP поверх битрейта
AUDIO_KBPS = 128
RAM_PER_VIEWER_MB = 0.15     # буферы TLS и соединения (замер: 211 МБ процессов на 1500 зрителей)
BASE_RAM_GB = 2


def recommend(viewers: int, video_kbps: int, streams: int = 1, headroom: float = 1.3, link_mbit: int | None = None) -> dict[str, Any]:
    """Оборудование узла для `viewers` одновременных зрителей (всего, на всех эфирах) при битрейте видео `video_kbps`."""
    per_viewer_mbit = (video_kbps + AUDIO_KBPS) * OVERHEAD / 1000
    gbps = viewers * per_viewer_mbit * headroom / 1000
    cores = max(2, math.ceil(gbps / TLS_GBPS_PER_CORE) + 1)
    ram = max(4, math.ceil(BASE_RAM_GB + viewers * RAM_PER_VIEWER_MB / 1024 + streams * 0.5))
    cache_gb = max(20, math.ceil(streams * (video_kbps + AUDIO_KBPS) / 8 / 1024 * 1200 / 1024 * 4))     # окно 20 минут на эфир ×4 запаса
    out = {"viewers": viewers, "video_kbps": video_kbps, "streams": streams, "per_viewer_mbit": round(per_viewer_mbit, 2),
           "egress_gbps": round(gbps, 2), "cpu_cores": cores, "ram_gb": ram, "disk_gb": cache_gb,
           "traffic_tb_per_hour": round(viewers * per_viewer_mbit * 3600 / 8 / 1_000_000, 2)}
    if link_mbit and link_mbit > 0:
        out["link_viewers"] = int(link_mbit * 0.8 / per_viewer_mbit)
    return out


def assess(facts: dict, video_kbps: int = 5000) -> dict[str, Any]:
    """Пригодность сервера по сведениям предварительной проверки: сколько зрителей он выдержит и что мешает."""
    per_viewer = (video_kbps + AUDIO_KBPS) * OVERHEAD / 1000
    notes: list[dict[str, str]] = []
    blockers: list[str] = []
    os_ok = f"{facts.get('os_id')}:{facts.get('os_version')}" in {"ubuntu:22.04", "ubuntu:24.04", "ubuntu:26.04", "debian:12", "debian:13"}
    if not os_ok:
        blockers.append(f"Система {facts.get('os_name') or '?'} не поддерживается: нужны Ubuntu 22.04/24.04/26.04 или Debian 12/13.")
    if facts.get("arch") not in ("amd64", "arm64"):
        blockers.append(f"Архитектура {facts.get('arch')} не поддерживается: нужны amd64 или arm64.")
    if not facts.get("systemd"):
        blockers.append("Нет systemd.")
    if not facts.get("sudo"):
        blockers.append("Нет прав администратора: учётная запись должна быть root или входить в sudo.")
    if facts.get("distro_nginx"):
        blockers.append("Установлен nginx из пакетов системы: узлу нужен nginx с nginx.org. Удалите прежний nginx (apt purge nginx nginx-common) или выберите другой сервер.")
    if facts.get("ports_busy", {}).get("443") and not facts.get("installed"):
        blockers.append("Порт 443 уже занят другой программой: узлу нужен свободный 443 (и 80 для выпуска сертификата).")
    cpus = int(facts.get("cpus") or 0)
    mem_gb = (facts.get("mem_bytes") or 0) / 2**30
    avail_gb = (facts.get("cache_fs_avail") or 0) / 2**30
    speeds = [n.get("speed_mbit", -1) for n in facts.get("nics") or [] if (n.get("speed_mbit") or -1) > 0]
    link = max(speeds) if speeds else None
    by_cpu = int(max(cpus - 1, 1) * TLS_GBPS_PER_CORE * 1000 / per_viewer)
    by_ram = int(max(mem_gb - BASE_RAM_GB, 0.5) * 1024 / RAM_PER_VIEWER_MB)
    by_link = int(link * 0.8 / per_viewer) if link else None
    viewers = min(x for x in (by_cpu, by_ram, by_link) if x is not None)
    if link is None:
        notes.append({"level": "warn", "text": "Скорость сетевого подключения сервер не сообщает (виртуальная сеть). Оценка — по процессору и памяти; "
                      "фактический предел — тариф провайдера: уточните скорость и объём исходящего трафика."})
    if avail_gb < 20:
        blockers.append(f"Свободно {avail_gb:.0f} ГБ в /var/cache: для кеша нужно не меньше 20 ГБ.")
    elif avail_gb < 100:
        notes.append({"level": "info", "text": f"Свободно {avail_gb:.0f} ГБ: кеш будет ограничен {max(10, int(avail_gb * 0.6))} ГБ."})
    rota = [d for d in (facts.get("disks") or {}).get("blockdevices", []) if d.get("type") == "disk" and str(d.get("rota")) in ("1", "True", "true")]
    if rota:
        notes.append({"level": "info", "text": "Есть вращающиеся диски: для холодного кеша при большой аудитории лучше NVMe/SSD."})
    if cpus < 2 or mem_gb < 3.5:
        notes.append({"level": "warn", "text": f"Мало ресурсов ({cpus} vCPU, {mem_gb:.1f} ГБ): узел подойдёт только для небольшой аудитории."})
    if facts.get("container"):
        notes.append({"level": "info", "text": "Сервер — контейнер: параметры ядра для большого числа соединений задаёт хост."})
    return {"ok": not blockers, "blockers": blockers, "notes": notes, "viewers": max(viewers, 0), "by_cpu": by_cpu, "by_ram": by_ram, "by_link": by_link,
            "link_mbit": link, "video_kbps": video_kbps, "cache_gb": max(10, min(int(avail_gb * 0.6), 500))}


def _hits(c: dict) -> int:
    """Ответы без обращения к источнику: сегменты из кеша nginx, плейлисты из микрокеша (и ожидавшие чужого запроса)."""
    return sum(c.get(k, 0) for k in ("cache_HIT", "cache_UPDATING", "cache_STALE", "pl_HIT", "pl_WAIT", "pl_STALE"))


def _misses(c: dict) -> int:
    return sum(c.get(k, 0) for k in ("cache_MISS", "cache_EXPIRED", "pl_MISS"))


# ------------------------------------------------------------------------------------------------ служба
class CdnService:
    def __init__(self, settings, settings_svc, redis, journal=None):
        self._s = settings
        self._svc = settings_svc
        self._redis = redis
        self._journal = journal
        self._nodes_cache: tuple[float, list[CdnNode]] = (0.0, [])

    # секреты узла: шифрование с привязкой к строке и полю
    def enc(self, node_id, field: str, value: str) -> str:
        return self._svc.box.encrypt(value, aad=f"cdn_node.{node_id}.{field}".encode())

    def dec(self, node: CdnNode, field: str) -> str:
        raw = getattr(node, f"{field}_enc")
        if not raw:
            return ""
        return self._svc.box.decrypt(raw, aad=f"cdn_node.{node.id}.{field}".encode())

    @property
    def master(self) -> str:
        return master_of(self._s)

    async def cfg(self, db):
        return await self._svc.get(db, "live")

    def origin_url(self, cfg) -> str:
        return (cfg.cdn_origin_url or self._s.app_public_url or "").rstrip("/")

    async def nodes(self, db, *, fresh: bool = False) -> list[CdnNode]:
        """Узлы для выдачи зрителям (кеш процесса 5 с: доступ к эфиру не должен делать лишний запрос к базе на каждого зрителя)."""
        at, rows = self._nodes_cache
        if fresh or time.monotonic() - at > 5:
            rows = list((await db.execute(select(CdnNode).order_by(CdnNode.created_at))).scalars())
            for r in rows:
                db.expunge(r)
            self._nodes_cache = (time.monotonic(), rows)
        return rows

    def invalidate(self) -> None:
        self._nodes_cache = (0.0, [])

    async def viewer_link(self, db, cfg, access: str, ttl_s: int, bucket_s: int, viewer: str, avoid: str | None = None) -> dict | None:
        """Ссылка зрителя на узел CDN: {"base", "path", "node"} или None (узлов нет или все недоступны — решает вызывающий)."""
        if not cfg.cdn_nodes_enabled:
            return None
        node = pick(await self.nodes(db), viewer, avoid)
        if node is None:
            return None
        path, exp = sign_path(self.master, int(cfg.cdn_key_gen), access, ttl_s, bucket_s=bucket_s)
        return {"base": f"https://{node.public_name}", "path": path, "expires_at": exp, "node": node.public_name}

    # ---------------------------------------------------------------------- настройки узла и ключи источника
    def node_config(self, node: CdnNode, cfg, *, cache_gb: int | None = None, ahead: bool = False) -> dict[str, Any]:
        return {"node_id": str(node.id), "version": NODE_VERSION, "public_name": node.public_name, "origin_url": self.origin_url(cfg),
                "origin_tls_verify": bool(cfg.cdn_origin_tls_verify), "pull_key": self.dec(node, "pull_key"),
                "api_token_sha256": hashlib.sha256(self.dec(node, "api_token").encode()).hexdigest(),
                "keys": public_keys(self.master, int(cfg.cdn_key_gen), ahead=ahead), "cache_max_gb": int(cache_gb or (node.assessment or {}).get("cache_gb") or 20),
                "tls_mode": node.tls_mode, "acme_email": node.acme_email or ""}

    def new_secrets(self, node: CdnNode) -> None:
        node.pull_key_enc = self.enc(node.id, "pull_key", secrets.token_urlsafe(32))
        node.api_token_enc = self.enc(node.id, "api_token", secrets.token_urlsafe(32))

    def keys_map_path(self) -> Path:
        return Path(os.environ.get("LIVE_DIR") or str(Path(self._s.data_dir) / "live")) / "nginx" / "cdn-keys.map"

    async def write_origin_keys(self, db) -> str | None:
        """Список ключей узлов для nginx источника (map $http_x_pgcdn_key): узлы в установке и работающие. Права — как у секрета подписи."""
        # узел ручной установки (учётная запись pgcdn) получает ключ сразу: его скрипт проверяет связь с источником раньше, чем узел подключат
        rows = (await db.execute(select(CdnNode).where(or_(CdnNode.state.in_(("installing", "ready", "error")), CdnNode.ssh_user == "pgcdn")))).scalars()
        lines = []
        for n in rows:
            try:
                k = self.dec(n, "pull_key")
            except Exception:  # noqa: BLE001 — повреждённая запись не должна закрыть источник остальным узлам
                continue
            if k:
                lines.append(f'"{k}" "{str(n.id)[:8]}";\n')
        text = "".join(sorted(lines))
        p = self.keys_map_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.read_text() == text:
                return None
            tmp = p.with_name(f".cdn-keys.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, text.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.chown(tmp, -1, SECRET_GID)
            except OSError:
                pass
            os.chmod(tmp, SECRET_MODE)
            os.replace(tmp, p)
        except OSError as exc:
            log.warning("Не удалось записать ключи узлов CDN для nginx", extra={"path": str(p)})
            return f"{p} не записан: {exc.strerror or exc}"
        return None

    # ---------------------------------------------------------------------- состояние узлов
    def _client(self, node: CdnNode) -> httpx.AsyncClient:
        # самоподписанный сертификат (испытательный стенд) не проверяется; ACME и свой сертификат — проверяются
        return httpx.AsyncClient(timeout=httpx.Timeout(5.0), verify=node.tls_mode != "selfsigned", follow_redirects=False)

    async def fetch_stats(self, node: CdnNode) -> dict:
        url = f"https://{node.public_name}/.pgcdn/stats"
        async with self._client(node) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {self.dec(node, 'api_token')}"})
        if r.status_code != 200:
            raise RuntimeError(f"узел ответил {r.status_code}")
        return r.json()

    @staticmethod
    def derive(raw: dict, prev: dict | None) -> dict:
        """Показатели для панели: скорости — по разности с прошлым опросом."""
        c = raw.get("counters") or {}
        sysd = raw.get("sys") or {}
        out: dict[str, Any] = {"at": raw.get("time"), "conn": (raw.get("conn") or {}).get("active", 0), "viewers": sum((raw.get("viewers") or {}).values()),
                               "per_stream": raw.get("viewers") or {}, "nginx": raw.get("nginx"), "njs": raw.get("njs"), "keys": raw.get("keys") or [],
                               "mem_total": (sysd.get("mem") or {}).get("total"), "mem_avail": (sysd.get("mem") or {}).get("available"),
                               "load": sysd.get("load"), "cpus": sysd.get("cpus"), "disk": sysd.get("disk"),
                               "req_total": c.get("req", 0), "bytes_total": c.get("bytes", 0), "origin_bytes_total": c.get("origin_bytes", 0),
                               "hits": _hits(c), "misses": _misses(c),
                               "denied": {k[5:]: v for k, v in c.items() if k.startswith("deny_")}, "_raw": {"counters": c, "sys": {"cpu": sysd.get("cpu"), "net": sysd.get("net")}, "time": raw.get("time")}}
        lookups = out["hits"] + out["misses"]
        out["hit_ratio"] = round(out["hits"] / lookups, 4) if lookups else None
        p = (prev or {}).get("_raw")
        if p and raw.get("time") and p.get("time") and raw["time"] > p["time"]:
            dt = raw["time"] - p["time"]
            pc = p.get("counters") or {}
            out["req_rate"] = round((c.get("req", 0) - pc.get("req", 0)) / dt, 1)
            out["out_mbit"] = round((c.get("bytes", 0) - pc.get("bytes", 0)) * 8 / dt / 1e6, 1)
            out["origin_mbit"] = round((c.get("origin_bytes", 0) - pc.get("origin_bytes", 0)) * 8 / dt / 1e6, 2)
            h, m = _hits(c) - _hits(pc), _misses(c) - _misses(pc)
            out["hit_ratio_now"] = round(h / (h + m), 4) if h + m > 0 else None
            pcpu, ccpu = (p.get("sys") or {}).get("cpu") or {}, sysd.get("cpu") or {}
            if pcpu and ccpu and ccpu.get("total", 0) > pcpu.get("total", 0):
                out["cpu_pct"] = round(100 * (1 - (ccpu["idle"] - pcpu["idle"]) / (ccpu["total"] - pcpu["total"])), 1)
            pn, cn = (p.get("sys") or {}).get("net") or {}, sysd.get("net") or {}
            if pn and cn:
                out["net_tx_mbit"] = round((cn.get("tx", 0) - pn.get("tx", 0)) * 8 / dt / 1e6, 1)
                out["net_rx_mbit"] = round((cn.get("rx", 0) - pn.get("rx", 0)) * 8 / dt / 1e6, 1)
        return out

    async def poll(self, db, node: CdnNode) -> None:
        now = datetime.now(timezone.utc)
        node.last_check_at = now
        try:
            raw = await self.fetch_stats(node)
            node.stats = self.derive(raw, node.stats)
            node.oks, node.fails = node.oks + 1, 0
            node.last_ok_at = now
            if (node.last_error or "").startswith("опрос:"):
                node.last_error = None
            if node.health != "up" and (node.oks >= UP_AFTER or node.health == "unknown"):
                if node.health == "down" and self._journal:
                    self._journal.emit("live", "cdn_node_up", level="info", message=f"Узел CDN {node.public_name} снова доступен", data={"node": node.public_name})
                node.health = "up"
        except Exception as exc:  # noqa: BLE001 — любая ошибка опроса = узел не ответил
            node.fails, node.oks = node.fails + 1, 0
            node.last_error = f"опрос: {str(exc)[:300]}"
            if node.health != "down" and node.fails >= DOWN_AFTER:
                node.health = "down"
                if self._journal:
                    self._journal.emit("live", "cdn_node_down", level="warn", message=f"Узел CDN {node.public_name} не отвечает: зрители переводятся на другие узлы",
                                       data={"node": node.public_name, "error": str(exc)[:200]})

    async def poll_all(self, session_maker) -> None:
        async with session_maker() as db:
            await self.write_origin_keys(db)                 # самовосстановление файла ключей (том пересоздан, ручная правка)
            for n in (await db.execute(select(CdnNode).where(CdnNode.state == "installing"))).scalars():
                if not await self._redis.exists(f"cdn:job:{n.id}"):     # процесс с заданием перезапустился: иначе узел навсегда «устанавливается»
                    n.state, n.last_error = "error", "Установка прервалась (перезапуск основного сервера). Повторный запуск безопасен."
            rows = list((await db.execute(select(CdnNode).where(CdnNode.state == "ready"))).scalars())
            await asyncio.gather(*(self.poll(db, n) for n in rows), return_exceptions=True)
            await db.commit()
        self.invalidate()

    async def run(self, session_maker) -> None:
        """Фоновый опрос (одна копия на кластер — через Leader)."""
        while True:
            try:
                await self.poll_all(session_maker)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Опрос узлов CDN не удался")
            await asyncio.sleep(POLL_S)


def new_node(**kw) -> CdnNode:
    return CdnNode(id=uuid.uuid4(), **kw)
