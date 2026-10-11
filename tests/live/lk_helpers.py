"""Общее для живых сценариев презентации: что видит сам сервер звонков (участники, права, дорожки) и перехватчики страницы (getUserMedia, токен входа)."""
from livekit import api as lkapi
from livekit.protocol import models as _m

LK_URL, LK_KEY, LK_SECRET = "http://127.0.0.1:7880", "devkey", "s" * 40

# счётчик вызовов getUserMedia и запись ответов входа в комнату (identity, токен, имя комнаты LiveKit)
INIT_HOOKS = r"""
window.__gum = 0; window.__tokens = []; window.__join = null;
(() => {
  const md = navigator.mediaDevices; if (!md) return;
  const g = md.getUserMedia.bind(md); md.getUserMedia = function (...a) { window.__gum++; return g(...a); };
  const f = window.fetch; window.fetch = async function (...a) {
    const r = await f.apply(this, a);
    try { const u = String((a[0] && a[0].url) || a[0]); if (/\/(rooms\/[^/]+|guest\/room\/[^/]+)\/join$/.test(u)) r.clone().json().then((j) => { window.__join = j; window.__tokens.push(j.token); }).catch(() => {}); } catch (e) {}
    return r;
  };
})();
"""


def rtc_name(src):
    return {_m.TrackSource.MICROPHONE: "mic", _m.TrackSource.CAMERA: "cam", _m.TrackSource.SCREEN_SHARE: "screen", _m.TrackSource.SCREEN_SHARE_AUDIO: "screen_audio"}.get(src, str(src))


async def lk_state(room_name):
    """Что видит сам сервер звонков: участники, их права и опубликованные дорожки."""
    async with lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET) as lk:
        resp = await lk.room.list_participants(lkapi.ListParticipantsRequest(room=room_name))
    return {p.identity: {"tracks": sorted(rtc_name(t.source) for t in p.tracks), "can_publish": p.permission.can_publish,
                         "sources": sorted(rtc_name(s) for s in p.permission.can_publish_sources), "name": p.name} for p in resp.participants}
