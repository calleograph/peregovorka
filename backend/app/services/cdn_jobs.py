"""Задания над узлами CDN: установка по SSH, сквозная проверка, применение настроек, обновление, удаление.

Одновременно над узлом идёт одно задание (замок Redis `cdn:job:<id>`, срок — предел задания). Учётные данные первоначальной установки
лежат в Redis зашифрованными не дольше `CRED_TTL` и удаляются после успешной установки; в базу и журналы не попадают.
Этапы пишутся в `cdn_nodes.stages` по мере выполнения — администратор видит, где остановилась установка и почему.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from ..models import CdnNode
from . import cdn_ssh
from .cdn import NODE_VERSION, UP_AFTER, sign_path

log = logging.getLogger(__name__)

CRED_TTL = 1800
JOB_TTL = 2400
STAGE_TITLES = {
    "connect": "Подключение по SSH", "check": "Система и права", "packages": "Пакеты nginx и njs", "files": "Файлы узла", "account": "Учётная запись управления",
    "tls": "Сертификат TLS", "system": "Параметры системы", "start": "Запуск nginx", "acme": "Сертификат Let's Encrypt", "selfcheck": "Связь с источником",
    "mgmt": "Управление ключом основного сервера", "test": "Проверка раздачи тестового эфира", "done": "Готово",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CdnJobs:
    def __init__(self, app):
        self.app = app
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def cdn(self):
        return self.app.state.cdn

    @property
    def redis(self):
        return self.app.state.redis

    def _sm(self):
        return getattr(self.app.state, "bg_session_maker", None) or self.app.state.session_maker

    # ---------------------------------------------------------------------- учётные данные установки
    async def put_creds(self, node_id, creds: dict) -> None:
        box = self.app.state.settings_svc.box
        await self.redis.set(f"cdn:cred:{node_id}", box.encrypt(json.dumps(creds), aad=f"cdn_cred.{node_id}".encode()), ex=CRED_TTL)

    async def get_creds(self, node_id) -> dict | None:
        raw = await self.redis.get(f"cdn:cred:{node_id}")
        if not raw:
            return None
        box = self.app.state.settings_svc.box
        try:
            return json.loads(box.decrypt(raw.decode() if isinstance(raw, bytes) else raw, aad=f"cdn_cred.{node_id}".encode()))
        except Exception:  # noqa: BLE001
            return None

    async def drop_creds(self, node_id) -> None:
        await self.redis.delete(f"cdn:cred:{node_id}")

    # ---------------------------------------------------------------------- замок
    async def lock(self, node_id) -> str | None:
        tok = secrets.token_hex(8)
        ok = await self.redis.set(f"cdn:job:{node_id}", tok, nx=True, ex=JOB_TTL)
        return tok if ok else None

    async def unlock(self, node_id, tok: str) -> None:
        cur = await self.redis.get(f"cdn:job:{node_id}")
        if cur is not None and (cur.decode() if isinstance(cur, bytes) else cur) == tok:
            await self.redis.delete(f"cdn:job:{node_id}")

    async def busy(self, node_id) -> bool:
        return bool(await self.redis.exists(f"cdn:job:{node_id}"))

    def spawn(self, node_id, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro, name=f"cdn-job-{node_id}")
        self._tasks[str(node_id)] = t
        t.add_done_callback(lambda _t, k=str(node_id): self._tasks.pop(k, None))

    # ---------------------------------------------------------------------- этапы
    @staticmethod
    def _stage(node: CdnNode, name: str, status: str, msg: str = "") -> None:
        stages = [dict(s) for s in (node.stages or [])]
        for s in stages:
            if s["name"] == name:
                s.update(status=status, msg=msg[:400] if msg else s.get("msg", ""), at=_now().isoformat())
                break
        else:
            stages.append({"name": name, "title": STAGE_TITLES.get(name, name), "status": status, "msg": msg[:400], "at": _now().isoformat()})
        node.stages = stages

    async def _save(self, node_id, **fields) -> CdnNode:
        async with self._sm()() as db:
            node = await db.get(CdnNode, node_id)
            stage = fields.pop("_stage", None)
            for k, v in fields.items():
                setattr(node, k, v)
            if stage:
                self._stage(node, *stage)
            await db.commit()
            await db.refresh(node)
            db.expunge(node)
        self.cdn.invalidate()
        return node

    # ---------------------------------------------------------------------- установка
    async def install(self, node_id, tok: str, actor: dict) -> None:
        """Полная установка узла (вызывается в фоне после «Установить»)."""
        sm = self._sm()
        t0 = time.monotonic()
        try:
            async with sm() as db:
                node = await db.get(CdnNode, node_id)
                cfg = await self.cdn.cfg(db)
                if not node.mgmt_key_enc:
                    priv, _pub = cdn_ssh.new_mgmt_key()
                    node.mgmt_key_enc = self.cdn.enc(node.id, "mgmt_key", priv)
                if not node.pull_key_enc or not node.api_token_enc:
                    self.cdn.new_secrets(node)
                node.state, node.stages, node.last_error, node.diag = "installing", [], None, None
                await db.commit()
                await self.cdn.write_origin_keys(db)                # источник должен узнать ключ узла до его самопроверки
                config = self.cdn.node_config(node, cfg)
                priv = self.cdn.dec(node, "mgmt_key")
                import asyncssh  # noqa: PLC0415

                config["mgmt_pubkey"] = asyncssh.import_private_key(priv).export_public_key("openssh").decode().strip()
                creds = await self.get_creds(node.id) or {}
                if node.tls_mode == "files":
                    config["tls_cert_pem"], config["tls_key_pem"] = creds.get("tls_cert_pem", ""), creds.get("tls_key_pem", "")
                host, port, user, hk = node.ssh_host, node.ssh_port, node.ssh_user, node.host_key
            if not creds.get("password") and not creds.get("private_key"):
                await self._save(node_id, state="error", last_error="Нет учётных данных установки (истёк срок 30 минут): введите пароль или ключ ещё раз.",
                                 _stage=("connect", "fail", "нет учётных данных"))
                return
            await self._save(node_id, _stage=("connect", "start", f"{user}@{host}:{port}"))
            try:
                s = await cdn_ssh.connect(host, port, user, hk, password=creds.get("password"), private_key=creds.get("private_key"))
            except cdn_ssh.SshError as e:
                await self._save(node_id, state="error", last_error=e.message, _stage=("connect", "fail", e.message))
                return
            await self._save(node_id, _stage=("connect", "ok", ""))
            failed: dict[str, str] = {}

            async def on_line(stage: str, status: str, msg: str) -> None:
                if status == "fail":
                    failed.setdefault(stage, msg)
                if stage != "done":                         # «Готово» — после проверки управления и раздачи, а не после скрипта
                    await self._save(node_id, _stage=(stage, status if status != "warn" else "ok", msg))

            try:
                ok, journal = await s.install(config, on_line)
            finally:
                await s.close()
            if not ok:
                stage, msg = next(iter(failed.items()), ("install", "установка прервалась"))
                await self._save(node_id, state="error", last_error=f"{STAGE_TITLES.get(stage, stage)}: {msg}"[:500], diag=journal)
                return
            await self._save(node_id, diag=journal, _stage=("mgmt", "start", "вход ключом pgcdn"))
            node = await self._save(node_id)
            try:
                rc, out, err = await cdn_ssh.mgmt(host, port, hk, priv, "status", timeout=120)
                st = json.loads(out) if rc == 0 else {}
                if rc != 0:
                    raise cdn_ssh.SshError("mgmt", (err or out).strip()[:300] or f"код {rc}")
            except (cdn_ssh.SshError, ValueError) as e:
                msg = getattr(e, "message", str(e))
                await self._save(node_id, state="error", last_error=f"Управление узлом ключом pgcdn не работает: {msg}", _stage=("mgmt", "fail", msg))
                return
            await self._save(node_id, version=st.get("version") or NODE_VERSION, generation=st.get("generation"), _stage=("mgmt", "ok", f"nginx {st.get('nginx')}"))
            res = await self.test(node_id)
            if not res["ok"]:
                await self._save(node_id, state="error", last_error=f"Проверка раздачи: {res['message']}")
                return
            await self._save(node_id, state="ready", health="up", oks=UP_AFTER, fails=0, last_ok_at=_now(), installed_at=_now(), last_error=None,
                             _stage=("done", "ok", f"за {int(time.monotonic() - t0)} с"))
            await self.drop_creds(node_id)
            self.app.state.journal.emit("live", "cdn_node_installed", level="info", message=f"Узел CDN {node.public_name} установлен и раздаёт эфиры",
                                        data={"node": node.public_name, "seconds": int(time.monotonic() - t0)})
        except Exception as exc:  # noqa: BLE001
            log.exception("Установка узла CDN не удалась")
            await self._save(node_id, state="error", last_error=f"Внутренняя ошибка: {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            await self.unlock(node_id, tok)
            async with sm() as db:
                await self.cdn.write_origin_keys(db)

    # ---------------------------------------------------------------------- сквозная проверка
    async def test(self, node_id) -> dict[str, Any]:
        """Тестовый «эфир» на источнике (плейлист и два сегмента по 2 МБ случайных данных) → ссылка узла → плейлист и сегмент через узел:
        байты совпадают с источником, второй запрос — из кеша узла, чужая подпись — 403, просроченная — 410. Тестовые файлы удаляются."""
        node = await self._save(node_id, _stage=("test", "start", ""))
        live = self.app.state.live
        acc = secrets.token_hex(12)
        d = live.live_dir() / "b" / f"_cdntest_{acc}"
        link = live.live_dir() / "acc" / acc
        steps: list[str] = []
        try:
            d.mkdir(parents=True)
            os.chmod(d, 0o755)
            seg = os.urandom(2 * 1024 * 1024)
            (d / "seg_0.ts").write_bytes(seg)
            (d / "seg_1.ts").write_bytes(os.urandom(2 * 1024 * 1024))
            (d / "index.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1.5,\nseg_0.ts\n#EXTINF:1.5,\nseg_1.ts\n")
            for f in d.iterdir():
                os.chmod(f, 0o644)
            os.symlink(f"../b/{d.name}", link)
            async with self._sm()() as db:
                cfg = await self.cdn.cfg(db)
            path, _ = sign_path(self.cdn.master, int(cfg.cdn_key_gen), acc, 300, bucket_s=60)
            prefix = path.rsplit("/", 1)[0] + "/"
            base = f"https://{node.public_name}"
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), verify=self.cdn.verify(node)) as c:
                r = await c.get(base + path)
                if r.status_code != 200 or "seg_0.ts" not in r.text:
                    raise RuntimeError(f"плейлист через узел: {r.status_code} {r.text[:100]!r}")
                steps.append("плейлист получен")
                t = time.monotonic()
                r = await c.get(base + prefix + "seg_0.ts")
                cold = time.monotonic() - t
                if r.status_code != 200 or hashlib.sha256(r.content).digest() != hashlib.sha256(seg).digest():
                    raise RuntimeError(f"сегмент через узел: {r.status_code}, {len(r.content)} байт — не совпадает с источником")
                t = time.monotonic()
                r2 = await c.get(base + prefix + "seg_0.ts")
                warm = time.monotonic() - t
                if r2.headers.get("x-cache") != "HIT":
                    raise RuntimeError(f"второй запрос сегмента не из кеша узла (X-Cache: {r2.headers.get('x-cache')})")
                steps.append(f"сегмент 2 МБ совпадает с источником: первый запрос {cold * 1000:.0f} мс ({len(seg) * 8 / cold / 1e6:.0f} Мбит/с), из кеша {warm * 1000:.0f} мс")
                bad = prefix.rsplit(".", 1)[0] + "." + "A" * 86 + "/"
                code = (await c.get(base + bad + "seg_0.ts")).status_code
                if code != 403:
                    raise RuntimeError(f"поддельная подпись: ожидался 403, получен {code}")
                old, _ = sign_path(self.cdn.master, int(cfg.cdn_key_gen), acc, -600, bucket_s=60)
                code = (await c.get(base + old)).status_code
                if code != 410:
                    raise RuntimeError(f"просроченная ссылка: ожидался 410, получен {code}")
                steps.append("поддельная подпись — 403, просроченная — 410")
            msg = "; ".join(steps)
            await self._save(node_id, _stage=("test", "ok", msg))
            return {"ok": True, "message": msg, "cold_ms": int(cold * 1000), "warm_ms": int(warm * 1000)}
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}" if not isinstance(exc, RuntimeError) else str(exc)
            if isinstance(exc, httpx.ConnectError):
                msg = f"узел {node.public_name} недоступен с основного сервера по HTTPS (имя в DNS, порт 443, сертификат): {exc}"
            await self._save(node_id, _stage=("test", "fail", msg[:400]))
            return {"ok": False, "message": msg[:400], "steps": steps}
        finally:
            try:
                link.unlink(missing_ok=True)
            except OSError:
                pass
            shutil.rmtree(d, ignore_errors=True)

    # ---------------------------------------------------------------------- управление
    async def _mgmt(self, node: CdnNode, sub: str, stdin: str = "", timeout: float = 120) -> tuple[int, str, str]:
        return await cdn_ssh.mgmt(node.ssh_host, node.ssh_port, node.host_key, self.cdn.dec(node, "mgmt_key"), sub, stdin=stdin, timeout=timeout)

    async def apply(self, node_id, *, ahead: bool = False) -> tuple[bool, str]:
        """Текущие настройки (ключи подписи, адрес источника, кеш) — на узел. ahead — с ключом следующего поколения (смена ключа)."""
        async with self._sm()() as db:
            node = await db.get(CdnNode, node_id)
            cfg = await self.cdn.cfg(db)
            conf = self.cdn.node_config(node, cfg, ahead=ahead)
            db.expunge(node)
        try:
            rc, out, err = await self._mgmt(node, "apply", json.dumps(conf), timeout=60)
        except cdn_ssh.SshError as e:
            return False, e.message
        if rc != 0:
            return False, (err or out).strip()[:400]
        gen = out.strip().split(" ")[-1]
        await self._save(node_id, generation=gen)
        return True, gen

    async def apply_all(self, *, ahead: bool = False) -> dict[str, str]:
        async with self._sm()() as db:
            ids = [n.id for n in (await db.execute(select(CdnNode).where(CdnNode.state == "ready"))).scalars()]
        res = await asyncio.gather(*(self.apply(i, ahead=ahead) for i in ids), return_exceptions=True)
        return {str(i): ("ok" if (not isinstance(r, BaseException) and r[0]) else (r[1] if not isinstance(r, BaseException) else str(r))) for i, r in zip(ids, res)}

    async def upgrade(self, node_id, tok: str) -> None:
        try:
            async with self._sm()() as db:
                node = await db.get(CdnNode, node_id)
                db.expunge(node)
            await self._save(node_id, stages=[], _stage=("packages", "start", "обновление nginx, njs и файлов узла"))
            try:
                rc, out, err = await self._mgmt(node, "upgrade", cdn_ssh.bundle_b64(), timeout=900)
            except cdn_ssh.SshError as e:
                await self._save(node_id, last_error=f"Обновление: {e.message}", _stage=("packages", "fail", e.message))
                return
            text = (out + err)[-6000:]
            if rc != 0:
                fails = [ln for ln in text.splitlines() if " fail " in ln]
                await self._save(node_id, last_error=f"Обновление: {(fails[-1] if fails else text.strip()[-300:])[:400]}", diag=text,
                                 _stage=("packages", "fail", "см. журнал"))
                return
            ok, gen = await self.apply(node_id)
            await self._save(node_id, version=NODE_VERSION, diag=text, last_error=None if ok else f"Применение настроек: {gen}",
                             _stage=("packages", "ok" if ok else "fail", "узел обновлён" if ok else gen))
        finally:
            await self.unlock(node_id, tok)

    async def uninstall(self, node_id) -> tuple[bool, str]:
        async with self._sm()() as db:
            node = await db.get(CdnNode, node_id)
            db.expunge(node)
        if not node.mgmt_key_enc or not node.host_key:
            return True, "узел не устанавливался"
        try:
            rc, out, err = await self._mgmt(node, "uninstall", timeout=120)
        except cdn_ssh.SshError as e:
            return False, e.message
        return rc == 0, (out or err).strip()[:300]
