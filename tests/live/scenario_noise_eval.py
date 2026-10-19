"""Сценарий (см. README.md): ИЗМЕРЕНИЕ шумоподавления на настоящем LiveKit с двумя участниками.
Участник A «говорит» файл noise_corpus (речь + шесть видов бытовых шумов, `--use-file-for-fake-audio-capture`), участник B принимает. На стороне B снимается уровень звука (дБ от полной шкалы,
блоки по 43 мс) именно из принятого потока — то, что слышит второй участник. Режимы A переключаются «на лету» из меню: выключено → стандартное → усиленное. По каждому шуму считаются
уровень шума в паузах, уровень речи, отношение речь/шум и подавление относительно «выключено». Результат — таблица в консоли и JSON (SMOKE_OUT/noise_eval.json).
Нужны: стенд с LiveKit, numpy (генерация звука). Не проверяет: настоящий микрофон и акустику комнаты, субъективную разборчивость."""
import asyncio, json, os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
import noise_corpus as C

S.ROOM = "/rooms/hr"
MODES = [("off", "Выключено"), ("standard", "Стандартное"), ("enhanced", "Усиленное")]
SETTLE, RECORD = int(os.environ.get("NOISE_SETTLE", "14")), int(os.environ.get("NOISE_RECORD", "125"))

METER = """(async () => {
  if (window.__meter) { try { window.__meter.proc.disconnect(); window.__meter.ctx.close(); } catch (e) {} }
  const el = [...document.querySelectorAll('audio')].find(a => a.srcObject && a.srcObject.getAudioTracks().length);
  if (!el) return 'no-audio';
  const ctx = new AudioContext({sampleRate: 48000}); await ctx.resume();
  const src = ctx.createMediaStreamSource(el.srcObject); const proc = ctx.createScriptProcessor(2048, 1, 1);
  const out = []; proc.onaudioprocess = (e) => { const d = e.inputBuffer.getChannelData(0); let s = 0, pk = 0; for (let i = 0; i < d.length; i++) { s += d[i] * d[i]; const a = Math.abs(d[i]); if (a > pk) pk = a; } out.push([s / d.length, pk]); };
  const g = ctx.createGain(); g.gain.value = 0; src.connect(proc); proc.connect(g); g.connect(ctx.destination);
  window.__meter = {ctx, proc, out, sr: ctx.sampleRate}; return 'ok';
})()"""
SENDER = """(() => window.__pcs.flatMap(pc => pc.getSenders()).filter(s => s.track && s.track.kind === 'audio').map(s => ({label: s.track.label, ns: s.track.getSettings().noiseSuppression, ec: s.track.getSettings().echoCancellation, agc: s.track.getSettings().autoGainControl, state: s.track.readyState})))()"""


async def menu_pick(c, group, item):
    async def esc():
        for t in ("keyDown", "keyUp"):
            await c.send("Input.dispatchKeyEvent", type=t, key="Escape", code="Escape", windowsVirtualKeyCode=27)
        await asyncio.sleep(0.3)
    await esc(); ok = await c.click_btn("Ещё"); await asyncio.sleep(0.4)
    g = await c.js(f"(() => {{ const b = [...document.querySelectorAll('.ctx-menu [role=menuitem]')].find(x => x.textContent.includes({group!r})); if (!b || b.disabled) return false; b.click(); return true; }})()")
    await asyncio.sleep(0.4)
    r = await c.js(f"(() => {{ const b = [...document.querySelectorAll('.ctx-sub [role=menuitemradio]')].find(x => x.textContent.replace('✓','').trim() === {item!r}); if (!b || b.disabled) return false; b.click(); return true; }})()")
    await asyncio.sleep(1.0)
    return bool(ok and g and r)


def analyse(blocks, sr, meta):  # noqa: E302
    """blocks: [[энергия, пик], …] по 2048 отсчётов. Фаза петли подбирается по маске «речь/пауза» (корреляция), затем уровни по каждому шуму."""
    dt = 2048 / sr
    e = np.array([b[0] for b in blocks]); pk = np.array([b[1] for b in blocks])
    db = 10 * np.log10(e + 1e-12)
    P = meta["period"]; n = len(db)
    t = np.arange(n) * dt
    def mask_at(tau):
        ph = (t - tau) % P
        sp = np.zeros(n, bool); no = np.zeros(n, bool); seg = np.full(n, -1)
        for k, s in enumerate(meta["segments"]):
            a, b = s["speech"]; sp |= (ph >= a + 0.3) & (ph < b - 0.2)
            for (x, y) in s["noise_only"]:
                no |= (ph >= x + 0.4) & (ph < y - 0.2)
                seg[(ph >= x) & (ph < y)] = k
            seg[(ph >= a) & (ph < b)] = k
        return sp, no, seg
    best, tau_b = -1e9, 0.0
    for tau in np.arange(0, P, 0.25):
        sp, no, _ = mask_at(tau)
        if sp.sum() < 10 or no.sum() < 10: continue
        sc = 10 * np.log10(e[sp].mean() + 1e-12) - 10 * np.log10(e[no].mean() + 1e-12)
        if sc > best: best, tau_b = sc, tau
    sp, no, seg = mask_at(tau_b)
    out = {}
    for k, s in enumerate(meta["segments"]):
        ns, ss = no & (seg == k), sp & (seg == k)
        if ns.sum() < 4 or ss.sum() < 4: out[s["kind"]] = None; continue
        out[s["kind"]] = {"noise_rms": 10 * np.log10(e[ns].mean() + 1e-12), "noise_peak": 20 * np.log10(pk[ns].max() + 1e-9), "speech_rms": 10 * np.log10(e[ss].mean() + 1e-12)}
    return out, best


async def main():
    wav = os.path.join(tempfile.mkdtemp(prefix="noise-"), "noise_corpus.wav")
    meta = C.build(wav)
    base_flags = os.environ.get("BROWSER_FLAGS", "")
    os.environ["BROWSER_FLAGS"] = f"{base_flags} --use-file-for-fake-audio-capture={wav}"
    A = S.Client("A", "root", "root-pass", 9741)
    await A.start()
    os.environ["BROWSER_FLAGS"] = base_flags
    B = S.Client("B", "alice", "alice-pass", 9742)
    await B.start()
    res = {}
    try:
        for c in (A, B):
            S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.st-cell.type-camera').length>=2", 40) for c in (A, B)])
        S.check("B принимает звук A", await B.wait_for("[...document.querySelectorAll('audio')].some(a => a.srcObject && a.srcObject.getAudioTracks().some(t => t.readyState === 'live'))", 30))
        await asyncio.sleep(6)
        d0 = await A.js(SENDER)
        S.check("по умолчанию (настройки не трогали) сразу применён «усиленный» режим: в WebRTC уходит обработанный трек", any("MediaStreamAudioDestinationNode" in x["label"] for x in d0), str(d0))
        for mode, label in MODES:
            ok = await menu_pick(A, "Шумоподавление", label)
            S.check(f"режим «{label}» выбран в меню у A", ok)
            await asyncio.sleep(SETTLE)
            snd = await A.js(SENDER)
            res[mode] = {"sender": snd}
            print(f"   исходящая дорожка A [{mode}]: {snd}", flush=True)
            if mode == "off": S.check("«Выключено»: браузерное шумоподавление реально выключено на исходящей дорожке", bool(snd) and snd[0].get("ns") is False, str(snd))
            if mode == "standard": S.check("«Стандартное»: браузерное шумоподавление включено, обработки приложением нет", bool(snd) and snd[0].get("ns") is True and "MediaStreamAudioDestinationNode" not in snd[0]["label"], str(snd))
            if mode == "enhanced": S.check("«Усиленное»: уходит обработанный трек (RNNoise + затвор), браузерное подавление выключено", bool(snd) and "MediaStreamAudioDestinationNode" in snd[0]["label"], str(snd))
            m = await B.js(METER)
            S.check(f"B: измеритель принятого звука запущен ({label})", m == "ok", str(m))
            await asyncio.sleep(RECORD)
            data = await B.js("({sr: window.__meter.sr, out: window.__meter.out})")
            an, sc = analyse(data["out"], data["sr"], meta)
            res[mode]["levels"] = an; res[mode]["blocks"] = len(data["out"]); res[mode]["phase_score"] = sc
            S.check(f"B: записано достаточно звука ({label})", len(data["out"]) > 23 * RECORD * 0.8 and sc > 6, f"блоков {len(data['out'])}, контраст речь/пауза {sc:.1f} дБ")
        # --- сохранение режима: выключить и включить микрофон, перезагрузить страницу и войти снова
        await A.click_btn("Микрофон"); await asyncio.sleep(1.2); await A.click_btn("Микрофон"); await asyncio.sleep(4)
        d1 = await A.js(SENDER)
        S.check("«Усиленное» сохраняется после выключения и включения микрофона", any("MediaStreamAudioDestinationNode" in x["label"] for x in d1), str(d1))
        await A.send("Page.reload"); await asyncio.sleep(3)
        await A.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        S.check("после перезагрузки страницы вход выполнен", await A.join(navigate=False))
        await asyncio.sleep(8)
        d2 = await A.js(SENDER)
        S.check("«Усиленное» сохраняется после перезагрузки и повторного входа", any("MediaStreamAudioDestinationNode" in x["label"] for x in d2), str(d2))
        errs = {c.label: (await c.state())["errs"] for c in (A, B)}
        S.check("ошибок страницы нет", not any(errs.values()), json.dumps(errs, ensure_ascii=False)[:300])
    finally:
        for c in (A, B):
            try: await c.close()
            except Exception: pass
    # --- итоговая таблица
    print("\nшум                          режим        шум в паузах   пик   речь    речь/шум   подавление шума   изменение речи")
    for kind in C.NOISES:
        base = (res.get("off", {}).get("levels") or {}).get(kind)
        for mode, label in MODES:
            r = (res.get(mode, {}).get("levels") or {}).get(kind)
            if not r: print(f"{C.NOISE_LABEL[kind]:28s} {label:12s} нет данных"); continue
            sup = (base["noise_rms"] - r["noise_rms"]) if base else float("nan"); dsp = (r["speech_rms"] - base["speech_rms"]) if base else float("nan")
            print(f"{C.NOISE_LABEL[kind]:28s} {label:12s} {r['noise_rms']:8.1f} дБ {r['noise_peak']:6.1f} {r['speech_rms']:6.1f} {r['speech_rms'] - r['noise_rms']:9.1f} дБ {sup:12.1f} дБ {dsp:14.1f} дБ")
    out = os.path.join(S.OUT, "noise_eval.json")
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1, default=float)
    print("JSON:", out)
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

METER_FN = analyse
if __name__ == "__main__":
    asyncio.run(main())
