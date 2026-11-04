"""Подключение к серверу узла CDN по SSH (asyncssh).

* Ключ сервера: сначала `host_key()` — администратор сверяет отпечаток SHA256; дальше каждое подключение принимает только этот ключ
  (подмена сервера или «человек посередине» — отказ, а не молчаливое подключение).
* Первоначальная установка: логин и пароль (или закрытый ключ) администратора сервера. Они нужны только на время установки; root-команды —
  через `sudo -S` (пароль идёт на стандартный вход sudo, в аргументы и журналы не попадает).
* Дальше — только учётная запись `pgcdn` с ключом ed25519 основного сервера: принудительная команда `pgcdn-ssh` разрешает лишь подкоманды `pgcdn-ctl`.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import shlex
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncssh

PKG = Path(__file__).resolve().parent.parent / "assets" / "cdn_node"
PKG_FILES = ("install.sh", "pgcdn-ctl", "pgcdn-ssh", "pgcdn.js", "nginx.conf.tpl", "VERSION")
CONNECT_TIMEOUT = 30
STAGE_RE = re.compile(r"^@@PGCDN (\S+) (start|ok|fail|warn) ?(.*)$")


class SshError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _explain(exc: BaseException) -> SshError:
    if isinstance(exc, SshError):
        return exc
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return SshError("host_key_mismatch", "Ключ сервера SSH не совпадает с подтверждённым: сервер переустановлен или подключение перехвачено. "
                                             "Если сервер переустанавливали — подключите узел заново и сверьте новый отпечаток.")
    if isinstance(exc, asyncssh.PermissionDenied):
        return SshError("auth_failed", "Сервер отклонил логин, пароль или ключ.")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return SshError("timeout", "Сервер не ответил вовремя.")
    if isinstance(exc, ConnectionRefusedError):
        return SshError("refused", "Порт SSH закрыт (соединение отклонено).")
    if isinstance(exc, OSError):
        return SshError("unreachable", f"Сервер недоступен: {exc.strerror or exc}.")
    if isinstance(exc, asyncssh.Error):
        return SshError("ssh", f"Ошибка SSH: {exc.reason if hasattr(exc, 'reason') else exc}")
    return SshError("ssh", f"Ошибка подключения: {exc}")


async def host_key(host: str, port: int) -> tuple[str, str]:
    """Открытый ключ сервера SSH и его отпечаток SHA256 (без входа на сервер)."""
    try:
        key = await asyncio.wait_for(asyncssh.get_server_host_key(host, port), CONNECT_TIMEOUT)
    except BaseException as exc:  # noqa: BLE001
        raise _explain(exc) from None
    if key is None:
        raise SshError("no_host_key", "Сервер не прислал ключ SSH.")
    return key.export_public_key("openssh").decode().strip(), key.get_fingerprint("sha256")


def new_mgmt_key() -> tuple[str, str]:
    """Ключ управления узлом: (закрытый OpenSSH, открытый для authorized_keys)."""
    k = asyncssh.generate_private_key("ssh-ed25519", comment="peregovorka-cdn")
    return k.export_private_key("openssh").decode(), k.export_public_key("openssh").decode().strip()


class Session:
    """Сеанс SSH с закреплённым ключом сервера. `password` нужен для sudo, если вход не под root."""

    def __init__(self, conn: asyncssh.SSHClientConnection, user: str, password: str | None):
        self.conn = conn
        self.user = user
        self._pw = password

    async def close(self) -> None:
        self.conn.close()
        try:
            await asyncio.wait_for(self.conn.wait_closed(), 5)
        except Exception:  # noqa: BLE001
            pass

    def _root(self, cmd: str) -> tuple[str, str]:
        """Команда от root и то, что пойдёт на стандартный вход перед данными (пароль sudo)."""
        if self.user == "root":
            return cmd, ""
        if self._pw:
            return f"sudo -S -p '' -- bash -c {shlex.quote(cmd)}", self._pw + "\n"
        return f"sudo -n -- bash -c {shlex.quote(cmd)}", ""

    async def run(self, cmd: str, *, root: bool = False, stdin: str = "", timeout: float = 60) -> tuple[int, str, str]:
        pre = ""
        if root:
            cmd, pre = self._root(cmd)
        try:
            r = await asyncio.wait_for(self.conn.run(cmd, input=pre + stdin, check=False), timeout)
        except BaseException as exc:  # noqa: BLE001
            raise _explain(exc) from None
        return r.exit_status if r.exit_status is not None else 255, str(r.stdout or ""), str(r.stderr or "")

    async def can_sudo(self) -> bool:
        if self.user == "root":
            return True
        rc, _, _ = await self.run("true", root=True, timeout=60)
        return rc == 0

    async def upload_package(self, extra: dict[str, str] | None = None) -> str:
        """Пакет узла во временный каталог (0700) на сервере; возвращает путь. Файлы с секретами — 0600."""
        rc, out, err = await self.run("mktemp -d /tmp/pgcdn.XXXXXXXX", timeout=60)
        d = out.strip()
        if rc != 0 or not d.startswith("/tmp/pgcdn."):
            raise SshError("upload", f"Не удалось создать временный каталог: {err.strip()[:200]}")
        try:
            async with self.conn.start_sftp_client() as sftp:
                for name in PKG_FILES:
                    await sftp.put(str(PKG / name), f"{d}/{name}")
                    await sftp.chmod(f"{d}/{name}", 0o755 if name in ("install.sh", "pgcdn-ctl", "pgcdn-ssh") else 0o644)
                for name, text in (extra or {}).items():
                    async with sftp.open(f"{d}/{name}", "w", attrs=asyncssh.SFTPAttrs(permissions=0o600)) as f:
                        await f.write(text)
        except BaseException as exc:  # noqa: BLE001
            await self.run(f"rm -rf {shlex.quote(d)}", timeout=60)
            raise _explain(exc) from None
        return d

    async def facts(self) -> dict:
        d = await self.upload_package()
        try:
            rc, out, err = await self.run(f"bash {d}/install.sh --facts", root=True, timeout=120)
            if rc != 0 and not out.strip():
                rc2, out2, _ = await self.run(f"bash {d}/install.sh --facts", timeout=60)        # без sudo: сведения неполные, но покажем их
                out = out2 if rc2 == 0 else out
            try:
                facts = json.loads(out.strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise SshError("facts", f"Сервер не вернул сведения: {(err or out).strip()[:300]}") from None
        finally:
            await self.run(f"rm -rf {shlex.quote(d)}", timeout=60)
        facts["sudo"] = await self.can_sudo()
        return facts

    async def install(self, config: dict, on_line: Callable[[str, str, str], Awaitable[None]], *, timeout: float = 1200) -> tuple[bool, str]:
        """install.sh --config: этапы передаются on_line(этап, состояние, текст) по мере выполнения. Возвращает (успех, журнал без секретов)."""
        d = await self.upload_package({"node.json": json.dumps(config)})
        log: list[str] = []
        ok = False
        cmd, pre = self._root(f"bash {d}/install.sh --config {d}/node.json; rc=$?; rm -rf {shlex.quote(d)}; exit $rc")
        try:
            async with self.conn.create_process(cmd, stderr=asyncssh.STDOUT) as proc:
                if pre:
                    proc.stdin.write(pre)
                proc.stdin.write_eof()

                async def read() -> None:
                    nonlocal ok
                    async for line in proc.stdout:
                        line = line.rstrip("\n")
                        log.append(line[:500])
                        m = STAGE_RE.match(line)
                        if m:
                            await on_line(m.group(1), m.group(2), m.group(3))
                            if m.group(1) == "done" and m.group(2) == "ok":
                                ok = True
                await asyncio.wait_for(read(), timeout)
                await asyncio.wait_for(proc.wait(), 30)
                ok = ok and proc.exit_status == 0
        except BaseException as exc:  # noqa: BLE001
            await self.run(f"rm -rf {shlex.quote(d)}", timeout=60)
            e = _explain(exc)
            log.append(f"[основной сервер] {e.message}")
            await on_line("connection", "fail", e.message)
        return ok, "\n".join(log[-400:])


async def connect(host: str, port: int, user: str, host_key_str: str, *, password: str | None = None, private_key: str | None = None) -> Session:
    trusted = asyncssh.import_public_key(host_key_str)
    kw: dict = {"username": user, "port": port, "known_hosts": ([trusted], [], []), "connect_timeout": CONNECT_TIMEOUT, "keepalive_interval": 15,
                "agent_path": None, "preferred_auth": "publickey,password,keyboard-interactive"}
    if private_key:
        try:
            kw["client_keys"] = [asyncssh.import_private_key(private_key)]
        except (asyncssh.KeyImportError, ValueError):
            raise SshError("bad_key", "Закрытый ключ не читается (нужен ключ OpenSSH или PEM без пароля).") from None
    else:
        kw["client_keys"] = None
    if password:
        kw["password"] = password
    try:
        conn = await asyncssh.connect(host, **kw)
    except BaseException as exc:  # noqa: BLE001
        raise _explain(exc) from None
    return Session(conn, user, password)


async def mgmt(host: str, port: int, host_key_str: str, private_key: str, sub: str, *, stdin: str = "", timeout: float = 120) -> tuple[int, str, str]:
    """Команда управления узлом ключом pgcdn: pgcdn-ctl <sub>."""
    s = await connect(host, port, "pgcdn", host_key_str, private_key=private_key)
    try:
        return await s.run(f"pgcdn-ctl {sub}", stdin=stdin, timeout=timeout)
    finally:
        await s.close()


def bundle_b64() -> str:
    """Пакет узла (tar.gz, base64) для `pgcdn-ctl upgrade`."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name in PKG_FILES:
            info = t.gettarinfo(str(PKG / name), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = 0o755 if name in ("install.sh", "pgcdn-ctl", "pgcdn-ssh") else 0o644
            with open(PKG / name, "rb") as f:
                t.addfile(info, f)
    return base64.b64encode(buf.getvalue()).decode() + "\n"


def bundle_bytes() -> bytes:
    return base64.b64decode(bundle_b64())
