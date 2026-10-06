"""Обезличивание (fail closed), LLM, хранилище, записи — без сети: httpx.MockTransport и временные каталоги."""
from __future__ import annotations

import json
import wave
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.anonymizer import AnonymizerClient, AnonymizerError, split_into_chunks
from app.integrations.llm import LlmClient, LlmError
from app.services.recordings import BYTES_PER_SEC, finalize_pcm_files
from app.services.settings import AnonymizerSettings, LlmSettings
from app.services.storage import LocalStorage, SmbStorage, StorageError, meeting_relpath, safe_component


def docclean(handler) -> AnonymizerClient:
    cfg = AnonymizerSettings(enabled=True, base_url="https://anon.test", token="tok", max_chunk_chars=500)
    return AnonymizerClient(cfg, transport=httpx.MockTransport(handler))


def ok_reply(req: httpx.Request, *, clean=True, transform=lambda t: t):
    body = json.loads(req.content)
    return httpx.Response(200, json={"ok": True, "text": transform(body["text"]), "report": {"total_replaced": 2},
                                     "verification": {"clean": clean, "leaked_values_count": 0 if clean else 3}})


async def test_docclean_success_sends_bearer_and_mode():
    seen = {}

    def handler(req):
        seen["auth"], seen["url"], seen["body"] = req.headers.get("authorization"), str(req.url), json.loads(req.content)
        return ok_reply(req, transform=lambda t: t.replace("Иван", "[ФИО_1]"))

    res = await docclean(handler).anonymize("Иван сказал привет")
    assert res.text == "[ФИО_1] сказал привет" and res.replaced == 2
    assert seen["auth"] == "Bearer tok" and "action=api_text" in seen["url"] and seen["body"]["mode"] == "ai_ready"


@pytest.mark.parametrize("case,code", [
    (lambda req: ok_reply(req, clean=False), "not_clean"),
    (lambda req: httpx.Response(500, text="boom"), "http_500"),
    (lambda req: httpx.Response(200, text="<html>stub</html>"), "invalid_json"),
    (lambda req: httpx.Response(200, json={"ok": True, "verification": {"clean": True}}), "missing_field"),
    (lambda req: httpx.Response(200, json={"ok": False}), "status_not_ok"),
    (lambda req: httpx.Response(401, json={"ok": False, "code": "UNAUTHORIZED"}), "api_unauthorized"),
    (lambda req: ok_reply(req, transform=lambda t: ""), "empty_result"),
])
async def test_anonymizer_fails_closed(case, code):
    with pytest.raises(AnonymizerError) as e:
        await docclean(case).anonymize("Секретный текст")
    assert e.value.code == code


async def test_anonymizer_not_configured_and_network_errors_fail_closed():
    with pytest.raises(AnonymizerError) as e:
        await AnonymizerClient(AnonymizerSettings()).anonymize("текст")
    assert e.value.code == "not_configured"

    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(AnonymizerError) as e2:
        await docclean(boom).anonymize("текст")
    assert e2.value.code.startswith("transport_")


async def test_long_text_is_chunked_by_lines_and_reassembled():
    calls = []

    def handler(req):
        calls.append(len(json.loads(req.content)["text"]))
        return ok_reply(req)

    text = "\n".join(f"[10:00:{i:02d}] Участник: реплика номер {i} " + "слово " * 10 for i in range(40))
    res = await docclean(handler).anonymize(text)
    assert res.text == text and len(calls) > 1 and max(calls) <= 500 and res.chunks == len(calls)
    assert "".join(split_into_chunks(text, 500)) == text


async def test_generic_profile_uses_configured_fields():
    cfg = AnonymizerSettings(enabled=True, profile="generic", base_url="https://g.test", endpoint="/clean", request_field="in.text",
                             response_field="out.value", status_field="ok", status_ok_value="true", auth_type="header",
                             auth_header_name="X-Key", token="k")

    def handler(req):
        body = json.loads(req.content)
        assert req.headers["x-key"] == "k" and body["in"]["text"] == "привет"
        return httpx.Response(200, json={"ok": True, "out": {"value": "[X]"}})

    res = await AnonymizerClient(cfg, transport=httpx.MockTransport(handler)).anonymize("привет")
    assert res.text == "[X]"


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
