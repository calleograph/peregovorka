"""LLM (в том числе обязательная защита секретов), хранилище, записи — без сети: httpx.MockTransport и временные каталоги."""
from __future__ import annotations

import json
import wave
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.llm import LlmClient, LlmError
from app.services.recordings import BYTES_PER_SEC, finalize_pcm_files
from app.services.settings import LlmSettings
from app.services.storage import LocalStorage, SmbStorage, StorageError, meeting_relpath, safe_component


async def test_llm_openai_compatible_and_anthropic_formats():
    def openai(req):
        assert req.url.path.endswith("/chat/completions") and req.headers["authorization"] == "Bearer K"
        body = json.loads(req.content)
        assert body["messages"][0]["role"] == "system" and body["provider"] == {"only": ["DeepInfra"]}
        return httpx.Response(200, json={"choices": [{"message": {"content": " Протокол "}}], "usage": {"prompt_tokens": 5, "completion_tokens": 7}})

    cfg = LlmSettings(enabled=True, type="openai_compatible", base_url="https://llm.test/v1", model="m", api_key="K", routing_provider="DeepInfra")
    r = await LlmClient(cfg, transport=httpx.MockTransport(openai)).complete("sys", "user")
    assert r.text == "Протокол" and r.prompt_tokens == 5

    def anthropic(req):
        assert req.url.path.endswith("/messages") and req.headers["x-api-key"] == "K" and "anthropic-version" in req.headers
        assert json.loads(req.content)["system"] == "sys"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Ответ"}], "usage": {"input_tokens": 1, "output_tokens": 2}})

    cfg2 = LlmSettings(enabled=True, type="anthropic", model="claude", api_key="K")
    assert (await LlmClient(cfg2, transport=httpx.MockTransport(anthropic)).complete("sys", "u")).text == "Ответ"


async def test_secrets_never_reach_the_model_whatever_the_caller_sends():
    """Единственная точка отправки в модель сама маскирует пароли, токены и ключи — даже если вызывающий код не обезличивал текст."""
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    cfg = LlmSettings(enabled=True, type="openai_compatible", base_url="https://llm.test/v1", model="m", api_key="K")
    client = LlmClient(cfg, transport=httpx.MockTransport(handler))
    await client.complete("Системный промпт, token: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                          "Иванов сказал: пароль от стенда Qw3rty!2026xZ, строка postgresql://app:Pa55word@db.example.org/prod")
    body = seen["body"]
    for leaked in ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "Qw3rty!2026xZ", "Pa55word"):
        assert leaked not in body, leaked
    assert "Иванов" in body, "персональные данные — по настройке комнаты; без неё имя остаётся"
    assert client.stats["secrets_masked"] == 3


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (429, "rate_limited"), (400, "bad_request"), (500, "http_500")])
async def test_llm_errors_are_classified(status, code):
    cfg = LlmSettings(enabled=True, type="openai", model="m", api_key="K")
    with pytest.raises(LlmError) as e:
        await LlmClient(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(status, json={}))).complete("s", "u")
    assert e.value.code == code


# ----------------------------------------------------------------------------- хранилище
def test_safe_component_blocks_path_tricks():
    assert safe_component("../..") == "_" and safe_component("..") == "room"
    assert "/" not in safe_component("../../etc/passwd")
    assert "/" not in safe_component("a/b\\c:d") and safe_component("   ") == "room" and safe_component("CON?") == "CON_"


def test_meeting_relpath_has_room_date_weekday_time():
    start = datetime(2026, 10, 6, 14, 5, tzinfo=timezone.utc)  # вторник
    assert meeting_relpath("Переговорка №1", start) == "Переговорка №1/2026-10-06, вторник/14-05"


def test_local_storage_writes_and_rejects_traversal(tmp_path):
    st = LocalStorage(str(tmp_path))
    loc = st.write_bytes("Комната/2026-10-06, вторник/14-05/protocol.txt", "привет".encode())
    assert (tmp_path / "Комната/2026-10-06, вторник/14-05/protocol.txt").read_text(encoding="utf-8") == "привет" and loc
    assert st.exists("Комната/2026-10-06, вторник/14-05/protocol.txt")
    for bad in ("../x.txt", "/abs.txt", "a/../../b.txt"):
        with pytest.raises(StorageError):
            st.write_bytes(bad, b"x")
    assert "возможна" in st.test()
    assert not list(tmp_path.glob(".peregovorka-write-test-*"))


def test_smb_unc_paths_are_built_from_share_and_base():
    smb = SmbStorage("fs01", "protocols", "meet/2026", "svc", "pw", "CORP")
    assert smb._unc() == "\\\\fs01\\protocols\\meet\\2026"
    assert smb._unc("Комната/2026-10-06, вторник/14-05/protocol.txt") == "\\\\fs01\\protocols\\meet\\2026\\Комната\\2026-10-06, вторник\\14-05\\protocol.txt"
    assert smb._user == "CORP\\svc"
    with pytest.raises(StorageError):
        smb._unc("../escape")


def test_smb_unreachable_server_gives_clean_error():
    with pytest.raises(StorageError) as e:
        SmbStorage("127.0.0.1", "share", "", "u", "p").write_bytes("a/b.txt", b"x")
    assert str(e.value).startswith("SMB")


# ------------------------------------------------------------------------------- записи
def test_pcm_becomes_valid_wav_in_structured_dir_and_short_files_are_dropped(tmp_path):
    room_dir = tmp_path / "m-abc"
    room_dir.mkdir()
    (room_dir / "u-alice.pcm").write_bytes(b"\x01\x00" * (BYTES_PER_SEC // 2 * 5))  # 5 с
    (room_dir / "u-bob.pcm").write_bytes(b"\x01\x00" * 100)  # щелчок — отбрасывается
    done = finalize_pcm_files(str(tmp_path), "m-abc", "Комната/2026-10-06, вторник/14-05", {"u-alice": "Алиса / A"})
    assert len(done) == 1 and done[0].duration_s == 5
    wav_path = tmp_path / done[0].rel_path
    assert wav_path.parent.name == "14-05" and "/" not in wav_path.name
    with wave.open(str(wav_path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000) and w.getnframes() == 80000
    assert not room_dir.exists(), "исходный PCM удалён"
