"""Служба распознавания и сквозное шифрование: ключ встречи доходит до подключения к LiveKit (общий ключ, PBKDF2 — как у браузера); без ключа E2EE не включается."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.config import AsrSettings
from app.manager import parse_session
from app.worker import RoomWorker, SessionInfo


@dataclass
class _KeyProviderOptions:
    shared_key: bytes | None = None


@dataclass
class _E2EEOptions:
    key_provider_options: _KeyProviderOptions = field(default_factory=_KeyProviderOptions)


@dataclass
class _RoomOptions:
    auto_subscribe: bool = True
    e2ee: _E2EEOptions | None = None


FAKE_RTC = SimpleNamespace(RoomOptions=_RoomOptions, E2EEOptions=_E2EEOptions, KeyProviderOptions=_KeyProviderOptions)


def _worker(key: str = "") -> RoomWorker:
    return RoomWorker(AsrSettings(), SessionInfo(meeting_id="m1", room_name="m-1", e2ee_key=key), queue=None, publisher=None, vad_factory=None)  # type: ignore[arg-type]


def test_session_payload_carries_key_and_repr_hides_it():
    info = parse_session("m1", json.dumps({"room_name": "m-abc", "e2ee_key": "секрет-ключ-встречи"}))
    assert info is not None and info.e2ee_key == "секрет-ключ-встречи"
    assert "секрет" not in repr(info)
    assert parse_session("m1", {"room_name": "m-abc"}).e2ee_key == ""


def test_room_options_enable_e2ee_only_with_key():
    plain = _worker()._room_options(FAKE_RTC)
    assert plain.auto_subscribe is False and plain.e2ee is None
    enc = _worker("k3y-passphrase")._room_options(FAKE_RTC)
    assert enc.auto_subscribe is False and enc.e2ee.key_provider_options.shared_key == b"k3y-passphrase"


def test_real_sdk_options_when_available():
    rtc = pytest.importorskip("livekit.rtc")
    enc = _worker("k3y-passphrase")._room_options(rtc)
    assert enc.e2ee is not None and enc.e2ee.key_provider_options.shared_key == b"k3y-passphrase"
    assert enc.e2ee.key_provider_options.key_derivation_function == rtc.e2ee.proto_e2ee.KeyDerivationFunction.PBKDF2
    assert _worker()._room_options(rtc).e2ee is None
