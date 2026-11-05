"""Внешние тома: каталог сервера (смонтированный NFS, SMB, диск) открывается приложению запросом к помощнику на хосте. Сокета Docker и прав root у приложения нет.

Запрос — только операция и путь (updates.Channel, проверка здесь и ещё раз в scripts/lib/vollib.sh). Помощник делает `mount --bind ПУТЬ $DATA_ROOT/ext/ИД`, и том появляется в работающем
контейнере как /data/ext/ИД: каталог подключён с распространением rslave (deployment/compose.yml). Состояние томов видно из файла volumes.json, который пишет помощник.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

from .settings import SettingsError
from .updates import Channel

WAIT_S = 45.0              # помощник опрашивает запросы раз в 3 с, проверка каталога занимает секунды; недоступный ресурс — до 10 с
EXT_DIR = "/data/ext"
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,200}$")


def volume_id(path: str) -> str:
    """То же имя каталога, что vol_id в scripts/lib/vollib.sh: приложение и помощник получают его независимо."""
    slug = re.sub(r"[^a-z0-9]+", "-", path.lstrip("/").lower()).strip("-")[:40]
    return f"{slug or 'vol'}-{hashlib.sha256(path.encode()).hexdigest()[:6]}"


class Volumes:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "updater"
        self.channel = Channel(str(self.dir))

    def _read(self, name: str) -> dict:
        try:
            d = json.loads((self.dir / name).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def state(self) -> dict:
        return self._read("volumes.json")

    def info(self, host_path: str | None) -> dict | None:
        """Состояние тома по сведениям помощника: attached | unreachable | detached, свободное место."""
        if not host_path:
            return None
        return next((v for v in self.state().get("items", []) if v.get("host_path") == host_path), {"state": "detached", "host_path": host_path})

    async def _call(self, op: str, arg: str, by: str) -> dict:
        hint = f"Проверьте его в «Администрирование → Обновления» или выполните на сервере: sudo scripts/volumes.sh attach {arg}"
        if not (await asyncio.to_thread(self.channel.status)).get("available"):
            raise SettingsError(f"Помощник на сервере не запущен. {hint}")
        rid = await asyncio.to_thread(self.channel.request, "volume", by=by, op=op, arg=arg)
        end = time.monotonic() + WAIT_S
        while time.monotonic() < end:
            res = await asyncio.to_thread(self._read, "volume-result.json")
            if res.get("request_id") == rid:
                return res
            await asyncio.sleep(0.5)
        raise SettingsError(f"Помощник на сервере не ответил. {hint}")

    async def attach(self, host_path: str, *, by: str = "admin") -> str:
        """Подключить том и вернуть путь внутри контейнера. Уже подключённый повторно не подключается."""
        if not PATH_RE.match(host_path) or ".." in host_path:
            raise SettingsError("Путь на сервере: абсолютный, только латиница, цифры и знаки . _ - /")
        cur = self.info(host_path) or {}
        if cur.get("state") == "attached":
            return str(cur["container_path"])
        res = await self._call("attach", host_path, by)
        if not res.get("ok"):
            raise SettingsError(str(res.get("error") or "Не удалось подключить том"))
        return f"{EXT_DIR}/{res['id']}"

    async def detach(self, host_path: str, *, by: str = "admin") -> None:
        cur = self.info(host_path) or {}
        if not cur.get("id"):
            return
        try:
            await self._call("detach", str(cur["id"]), by)
        except SettingsError:
            pass                                 # том останется подключённым: данным это не вредит
