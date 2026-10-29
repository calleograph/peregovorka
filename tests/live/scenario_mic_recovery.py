"""Сценарий (см. README.md): микрофон посреди встречи пропадает и возвращается сам.

Два браузера с поддельными устройствами и настоящим LiveKit. У Алисы дорожка микрофона «заканчивается» (как при отключении гарнитуры или захвате устройства другой программой),
а getUserMedia какое-то время отвечает NotReadableError («занято»). Ожидается: понятное сообщение вместо молчаливого «Микрофон выкл.»; повторные попытки; когда устройство
освобождается — микрофон включается сам, звук снова идёт к Борису. Затем: пользователь сам выключил микрофон — автоматических попыток нет.
Не проверяет настоящее устройство Windows и монопольный режим: событие и отказ getUserMedia здесь подставные.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/sales"
HOOK = r"""
window.__gumTracks = []; window.__gumFail = 0; window.__gumCalls = 0;
const _gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (c) => {
  window.__gumCalls++;
  if (c && c.audio && window.__gumFail > 0) throw new DOMException('Could not start audio source', 'NotReadableError');
  const s = await _gum(c);
  s.getAudioTracks().forEach(t => window.__gumTracks.push(t));
  return s;
};
"""
NOTICE = "[...document.querySelectorAll('.alert')].map(a => a.textContent).join(' | ')"
MIC_ON = "(() => { const b = document.querySelector('button[aria-label^=\"Микрофон\"]'); return !!b && b.getAttribute('aria-pressed') === 'true'; })()"


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9521)
    B = S.Client("bob", "bob", "bob-pass", 9522)
    try:
        for c in (A, B):
            await c.start()
        await A.send("Page.addScriptToEvaluateOnNewDocument", source=HOOK)
        for c in (A, B):
            S.check(f"{c.label}: вошёл", await c.join())
        ok = await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        S.check("оба в комнате", all(ok))
        S.check("у Алисы микрофон включён", await A.wait_for(MIC_ON, 20)); await asyncio.sleep(3)
        s0 = await B.js(S.STATS)
        S.check("Борис слышит Алису (пакеты звука идут)", s0["ain"] > 20, str(s0))

        # --- сбой: дорожка «заканчивается», устройство занято
        await A.js("(() => { window.__gumFail = 1000; const tr = window.__gumTracks.filter(x => x.readyState === 'live').pop(); window.__ended = !!tr; if (tr) { tr.stop(); tr.dispatchEvent(new Event('ended')); } })()")
        S.check("поддельное «устройство пропало» доставлено дорожке", await A.js("window.__ended"), str(await A.js("window.__gumTracks.map(t => t.readyState + (t.muted ? '/muted' : '') + (t.enabled ? '' : '/disabled'))")))
        shown = await A.wait_for("document.body.innerText.includes('Микрофон перестал работать')", 12)
        S.check("вместо молчаливого выключения показано объяснение", shown, (await A.js(NOTICE))[:200])
        await A.shot("mic-lost")
        S.check("кнопка микрофона показывает «выключен» и пользователю понятно, что идёт восстановление", not await A.js(MIC_ON))
        await asyncio.sleep(4)
        calls = await A.js("window.__gumCalls")
        S.check("идут повторные попытки открыть устройство", calls >= 3, f"вызовов getUserMedia: {calls}")

        # --- устройство освободилось
        await A.js("window.__gumFail = 0")
        back = await A.wait_for("document.body.innerText.includes('Микрофон снова работает')", 30)
        S.check("когда устройство освободилось, микрофон включился сам", back, (await A.js(NOTICE))[:200])
        S.check("кнопка микрофона снова «включён»", await A.wait_for(MIC_ON, 10))
        base = (await B.js(S.STATS))["ain"]; await asyncio.sleep(4); after = (await B.js(S.STATS))["ain"]
        S.check("Борис снова получает звук Алисы", after - base > 50, f"{base} → {after}")
        await A.shot("mic-recovered")

        # --- пользователь выключил микрофон сам: автоматических попыток быть не должно
        await A.click_btn("Микрофон"); await asyncio.sleep(1.5)
        S.check("микрофон выключен пользователем", not await A.js(MIC_ON))
        c0 = await A.js("window.__gumCalls"); await asyncio.sleep(6)
        S.check("после ручного выключения устройство не открывается заново", await A.js("window.__gumCalls") == c0)

        errs = [e for e in (await A.state())["errs"] if "Uncaught" in e or "hooks" in e]
        S.check("ошибок страницы нет", not errs, str(errs[:3]))
    finally:
        for c in (A, B):
            await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
