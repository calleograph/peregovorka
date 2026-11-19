#!/usr/bin/env python3
"""Показ экрана в обычной комнате при узком исходящем канале ведущего (тестовый экземпляр, настоящий Chromium под Xvfb и зритель в headless).
Канал сужается потолком битрейта на отправителе (400 кбит/с): так кодировщик попадает в то же положение, что при плохой сети.
До 0.76.5 показ шёл двумя слоями: верхний 1080p не отправлялся, зритель получал 720p/5 к/с, а при узком канале — 541 и 427×231 строк («кубики»).
  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox DISPLAY=:98 (Xvfb 2560x1440) PEREGOVORKA_URL=http://localhost:28480 python screen_quality_check.py"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import browser_chain as B
import smoke_room as S

CAP_KBPS = 400
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


# таблица с прокруткой раз в 0,7 с — как документ, который листают при показе
TABLE = r"""(() => {
  const c = document.createElement('canvas'); c.width = innerWidth; c.height = innerHeight;
  Object.assign(c.style, {position: 'fixed', inset: 0, zIndex: 99999}); document.body.append(c);
  const g = c.getContext('2d'); let off = 0;
  const draw = () => { g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.font = '15px sans-serif';
    for (let r = 0; r < 70; r++) for (let k = 0; k < 9; k++) { g.fillStyle = (r + k) % 7 ? '#222' : '#1a5fd0';
      g.fillText('Ячейка ' + (r + off) + '.' + k + ' сервис владелец', 20 + k * 270, 30 + r * 22); g.strokeStyle = '#ccc'; g.strokeRect(14 + k * 270, 14 + r * 22, 270, 22); } };
  draw(); setInterval(() => { off++; draw(); }, 700);
})()"""

SENDER = r"""(async cap => {
  for (const pc of window.__pcs) for (const s of pc.getSenders()) {
    if (!s.track || s.track.kind !== 'video') continue;
    const p = s.getParameters();
    p.encodings.forEach(e => { e.maxBitrate = cap * 1000; });
    await s.setParameters(p);
    return {layers: p.encodings.length, hint: s.track.contentHint};
  }
  return null;
})"""

INBOUND = r"""(async () => { let best = null; for (const pc of window.__pcs) { const st = await pc.getStats(); st.forEach(r => {
  if (r.type === 'inbound-rtp' && r.kind === 'video' && r.frameWidth && (!best || r.frameWidth > best[0])) best = [r.frameWidth, r.frameHeight, r.framesPerSecond || 0]; }); } return best; })()"""


async def main():
    api = B.admin()
    slug = f"scrq-{time.strftime('%H%M%S')}"
    rid = api.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "regular", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    S.ROOM = f"/rooms/{slug}"
    p = B.Presenter("scrq", "admin", "-", 9895)
    p.cookie = api.headers["Cookie"].split("=", 1)[1]
    v = None
    try:
        await p.start()
        await p.goto(S.ROOM, 2.5)
        for _ in range(60):
            if await p.js("!!document.querySelector('.rbar')"): break
            await p.js("[...document.querySelectorAll('button')].find(b => /^(Присоединиться|Войти в переговорку)/.test(b.textContent.trim()))?.click()")
            await p.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            await asyncio.sleep(1)
        await asyncio.sleep(2)
        t("ведущий начал показ экрана", await p.click_btn("Показать экран")
          and await p.wait_for("window.__pcs.some(pc => pc.getSenders().some(s => s.track && s.track.kind === 'video'))", 30))
        await asyncio.sleep(2)
        await p.js(TABLE)
        snd = await p.js(f"({SENDER})({CAP_KBPS})")
        t("показ идёт одним слоем", bool(snd) and snd["layers"] == 1, str(snd))
        t("подсказка кодировщику — «текст» (SDK ставит «движение»)", bool(snd) and snd["hint"] == "text", str(snd))
        S.INIT += r"""navigator.mediaDevices.getDisplayMedia = async () => { const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d'); let n = 0;
  setInterval(() => { g.fillStyle = '#234'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '64px sans-serif'; g.fillText('ВТОРОЙ ЭКРАН ' + (n++), 100, 300); }, 100); return c.captureStream(10); };"""
        v = S.Client("viewer", "admin", open(os.environ["ADMIN_PW_FILE"]).read().strip(), 9896)
        await v.start()
        t("зритель вошёл", await v.join())
        await asyncio.sleep(15)                                  # кодировщик подстраивается под потолок
        got = []
        for _ in range(6):
            x = await v.js(INBOUND)
            if x: got.append(x)
            await asyncio.sleep(3)
        print("зритель получает (ширина, высота, к/с):", got)
        t(f"у зрителя полное разрешение 1080 строк при {CAP_KBPS} кбит/с", len(got) >= 4 and min(g[1] for g in got) >= 1080, str(sorted({g[1] for g in got})))
        t("у зрителя не меньше 6 к/с", len(got) >= 4 and sum(g[2] for g in got) / len(got) >= 6, str([g[2] for g in got]))
        await v.click_btn("Показать экран")                     # второй показ одновременно с первым (предел — пять, «только один» выключен)
        await asyncio.sleep(4)
        err = await v.js("[...document.querySelectorAll('.ctl-err, [role=alert]')].map(e => e.textContent).join(' | ')")
        t("второй участник показывает экран одновременно с первым", await v.js("!!document.querySelector('button[aria-label=\"Остановить показ\"]')") and "уже показывает" not in (err or ""), err or "")
        t("ведущий видит чужой показ", await p.wait_for("document.querySelectorAll('.screen-stage video').length >= 1", 15))
    finally:
        for c in (v, p):
            try:
                if c: await c.close()
            except Exception: pass
        api.delete(f"/api/v1/admin/rooms/{rid}")
    print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
