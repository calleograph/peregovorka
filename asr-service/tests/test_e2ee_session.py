"""Служба распознавания и сквозное шифрование: ключ встречи доходит до подключения к LiveKit (общий ключ, PBKDF2 — как у браузера); без ключа E2EE не включается."""
from __future__ import annotations

import json

from livekit import rtc

from app.config import AsrSettings
from app.manager import parse_session
from app.worker import RoomWorker, SessionInfo


def _worker(key: str = "") -> RoomWorker:
    s = AsrSettings()
    return RoomWorker(s, SessionInfo(meeting_id="m1", room_name="m-1", e2ee_key=key), queue=None, publisher=None, vad_factory=None)  # type: ignore[arg-type]


def test_session_payload_carries_key_and_repr_hides_it():
    info = parse_session("m1", json.dumps({"room_name": "m-abc", "e2ee_key": "секрет-ключ-встречи"}))
    assert info is not None and info.e2ee_key == "секрет-ключ-встречи"
    assert "секрет" not in repr(info)
    assert parse_session("m1", {"room_name": "m-abc"}).e2ee_key == ""


def test_room_options_enable_e2ee_only_with_key():
    plain = _worker()._room_options(rtc)
    assert plain.auto_subscribe is False and plain.e2ee is None
    enc = _worker("k3y-passphrase")._room_options(rtc)
    assert enc.auto_subscribe is False and enc.e2ee is not None
    assert enc.e2ee.key_provider_options.shared_key == b"k3y-passphrase"
    assert enc.e2ee.key_provider_options.key_derivation_function == rtc.e2ee.proto_e2ee.KeyDerivationFunction.PBKDF2
