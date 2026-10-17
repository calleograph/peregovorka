"""Сценарий (см. README.md): сквозное шифрование звука и видео (LiveKit E2EE) на настоящем сервере звонков.

Два браузера входят в комнату с режимом «доверенный компонент»: индикатор «сквозное (E2EE)», звук и видео друг друга декодируются (расшифровываются в браузере).
Затем «внешний наблюдатель» — скрытый участник на Python SDK (так же устроена служба распознавания) — подключается к комнате:
  • с ключом встречи слышит звук (расшифровывает сам — так работает доверенный компонент);
  • с неверным ключом не получает звука (сервер звонков и посторонний ключа не имеют).
Не проверяется: Safari/Firefox (нужен Chromium с поддельными устройствами), запись видео (Egress), SIP."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import smoke_room as S
from livekit import api as lkapi, rtc
from lk_helpers import INIT_HOOKS, LK_KEY, LK_SECRET

SET = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); const h = {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token};
  const rooms = await (await fetch('/api/v1/admin/rooms')).json(); const room = rooms.find(r => r.slug === 'sales');
  const r = await fetch('/api/v1/admin/rooms/' + room.id, {method: 'PATCH', headers: h, body: JSON.stringify({e2ee_mode: 'trusted'})});
  return r.status; })()"""


async def probe(room_name: str, key: str | None, seconds: float = 8.0) -> tuple[float, float]:
    """Скрытый подписчик: (громкость, «рваность» уровня) принятого звука. Расшифрованный сигнал поддельного микрофона — гудки с паузами (уровень скачет);
    если пакеты не расшифрованы, декодер выдаёт равномерный шум; при неверном ключе кадры отбрасываются — тишина."""
    token = (lkapi.AccessToken(LK_KEY, LK_SECRET).with_identity("probe-" + (key or "none")[:6]).with_grants(
        lkapi.VideoGrants(room_join=True, room=room_name, hidden=True, can_subscribe=True, can_publish=False)).to_jwt())
    opts = rtc.RoomOptions(auto_subscribe=True, encryption=rtc.E2EEOptions(key_provider_options=rtc.KeyProviderOptions(shared_key=key.encode())) if key else None)
    room, chunks, tasks = rtc.Room(), [], []

    async def read(track):
        async for ev in rtc.AudioStream(track, sample_rate=16000, num_channels=1):
            chunks.append(np.frombuffer(ev.frame.data, dtype=np.int16).astype(np.float64))

    @room.on("track_subscribed")
    def _sub(track, pub, part):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(read(track)))
    await room.connect("ws://127.0.0.1:7880", token, options=opts)
    await asyncio.sleep(seconds)
    for t in tasks:
        t.cancel()
    await room.disconnect()
    if not chunks:
        return 0.0, 0.0
    x = np.concatenate(chunks)
    win = 1600                                   # окна по 0,1 с
    lv = np.array([float(np.sqrt((x[i:i + win] ** 2).mean())) for i in range(0, len(x) - win + 1, win)])
    if lv.size < 5:
        return 0.0, 0.0
    # поддельный микрофон гудит паузами (тон — тишина), то есть уровень «рваный»; расшифрованный шифртекст — равномерный шум
    return float(np.percentile(lv, 90)), float(np.percentile(lv, 90) / (np.percentile(lv, 10) + 1.0))


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    A = S.Client("alice", "alice", "alice-pass", 9495)
    B = S.Client("bob", "bob", "bob-pass", 9496)
    try:
        for c in (R, A, B):
            await c.start()
        await A.send("Page.addScriptToEvaluateOnNewDocument", source=INIT_HOOKS)
        S.check("администратор включил E2EE (доверенный компонент)", (await R.js(SET)) == 200)
        for c in (A, B):
            S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        badge = await A.wait_for("(() => { const b = document.querySelector('.prot-badge'); return !!b && b.textContent.includes('сквозное (E2EE)'); })()", 20)
        S.check("индикатор: «ШИФРОВАНИЕ ВКЛЮЧЕНО · сквозное (E2EE)»", badge, await A.js("document.querySelector('.prot-badge')?.textContent"))
        S.check("камера Боба включена", await B.click_btn("Камера выкл."))
        await asyncio.sleep(5)
        sa, sb = await A.js(S.STATS), await B.js(S.STATS)
        S.check("звук идёт в обе стороны и декодируется", sa["ain"] > 30 and sb["ain"] > 30, f"{sa} {sb}")
        S.check("видео Боба расшифровывается и декодируется у Алисы", sa["vin"] > 10, str(sa))
        join = await A.js("window.__join")
        room_name, key = join["livekit_room"], join["e2ee"]["key"]
        S.check("в ответе входа есть ключ встречи (режим trusted)", join["e2ee"]["mode"] == "trusted" and len(key) >= 40)
        g_rms, g_pure = await probe(room_name, key)
        b_rms, b_pure = await probe(room_name, "совсем-другой-ключ-" + key[:10])
        n_rms, n_pure = await probe(room_name, None)
        print(f"probe: с ключом rms={g_rms:.0f} рваность={g_pure:.1f}; неверный ключ rms={b_rms:.0f} рваность={b_pure:.1f}; без ключа rms={n_rms:.0f} рваность={n_pure:.1f}")
        S.check("доверенный компонент с ключом слышит настоящий сигнал (гудки с паузами)", g_rms > 300 and g_pure > 500, f"громкость={g_rms:.0f} рваность={g_pure:.0f}")
        S.check("наблюдатель с неверным ключом ничего не слышит (кадры отбрасываются)", b_rms < 30, f"громкость={b_rms:.0f}")
        S.check("наблюдатель без ключа (как сам сервер звонков) получает только шум шифртекста", n_pure < 100, f"громкость={n_rms:.0f} рваность={n_pure:.0f}")
        await A.shot("e2ee-room")
        S.check("ошибок страницы нет", not (await A.state())["errs"] and not (await B.state())["errs"], str((await A.state())["errs"][:2]))
    finally:
        for c in (R, A, B):
            await c.close()
    bad_ = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad_)} из {len(S.results)}; не прошли: {[b[0] for b in bad_]}")

asyncio.run(main())
