"""HTTP-интерфейс службы ретрансляции: только внутри сети проекта, вход по внутреннему токену. Ключи площадок хранятся в памяти и в журнал не попадают."""
from __future__ import annotations

import hmac
import os
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from .manager import RelayManager

SRC_PREFIX = os.environ.get("RELAY_SRC_PREFIX", "rtmp://mediamtx:1935/")
ID = re.compile(r"^[0-9a-f-]{8,40}$")


class Put(BaseModel):
    src: str
    url: str
    audio: bool = False
    low: bool = False


manager = RelayManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await manager.close()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def authed(x_relay_token: str = Header("")) -> None:
    want = os.environ.get("INTERNAL_API_TOKEN", "")
    if not want or not hmac.compare_digest(x_relay_token, want):
        raise HTTPException(401)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "relays": len(manager.relays)}


@app.get("/relays", dependencies=[Depends(authed)])
async def relays() -> list[dict]:
    return manager.status()


@app.put("/relays/{rid}", status_code=204, dependencies=[Depends(authed)])
async def put(rid: str, body: Put) -> Response:
    if not ID.match(rid) or not body.src.startswith(SRC_PREFIX) or re.search(r"\s", body.src + body.url):
        raise HTTPException(422, "недопустимые параметры")
    if not body.url.startswith(("rtmp://", "rtmps://")) or len(body.url) > 700:
        raise HTTPException(422, "адрес площадки: rtmp:// или rtmps://")
    manager.put(rid, body.src, body.url, body.audio, body.low)
    return Response(status_code=204)


@app.delete("/relays/{rid}", status_code=204, dependencies=[Depends(authed)])
async def delete(rid: str) -> Response:
    await manager.delete(rid)
    return Response(status_code=204)
