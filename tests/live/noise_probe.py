"""Локальная проверка конвейера шумоподавления БЕЗ LiveKit: файл noise_corpus → getUserMedia (ограничения браузера) → (необязательно) обработчик приложения (RNNoise из /src, dev-сервер Vite) →
измеритель. Так быстро сравниваются настройки. Запуск: python noise_probe.py [имя_конфигурации …]. Нужен стенд (vite) и numpy."""
import asyncio, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import smoke_room as S
import noise_corpus as C
from scenario_noise_eval import analyse, METER_FN

RECORD = int(os.environ.get("PROBE_SECONDS", "75"))
CONFIGS = {
    "raw":      {"c": {"noiseSuppression": False, "echoCancellation": False, "autoGainControl": False}, "p": "none"},
    "ns":       {"c": {"noiseSuppression": True, "echoCancellation": False, "autoGainControl": False}, "p": "none"},
    "ns+agc":   {"c": {"noiseSuppression": True, "echoCancellation": False, "autoGainControl": True}, "p": "none"},
    "app-off":  {"c": {"noiseSuppression": False, "echoCancellation": True, "autoGainControl": True}, "p": "none"},
    "app-std":  {"c": {"noiseSuppression": True, "echoCancellation": True, "autoGainControl": True}, "p": "none"},
    "rnn":      {"c": {"noiseSuppression": False, "echoCancellation": True, "autoGainControl": True}, "p": "rnnoise"},
    "rnn-raw":  {"c": {"noiseSuppression": False, "echoCancellation": False, "autoGainControl": False}, "p": "rnnoise"},
    "rnn+gate": {"c": {"noiseSuppression": False, "echoCancellation": True, "autoGainControl": True}, "p": "rnnoise+gate"},
    "rnn+ns":   {"c": {"noiseSuppression": True, "echoCancellation": True, "autoGainControl": True}, "p": "rnnoise"},
}

PIPE = """(async (cfg, pipe) => {
  const stream = await navigator.mediaDevices.getUserMedia({audio: cfg});
  const tr = stream.getAudioTracks()[0]; const st = tr.getSettings();
  let out = stream;
  if (pipe !== 'none') {
    const mod = await import('/src/audio/rnnoise.ts'); const P = new mod.RnnoiseProcessor();
    await P.init({track: tr, kind: 'audio'}); out = new MediaStream([P.processedTrack]);
    window.__proc = P;
    if (pipe === 'rnnoise+gate') {
      const ctx2 = new AudioContext({sampleRate: 48000}); await ctx2.resume(); await ctx2.audioWorklet.addModule('/audio/noise-gate.worklet.js');
      const gate = new AudioWorkletNode(ctx2, 'pg-noise-gate', {processorOptions: window.__gateOpts || {}});
      const s2 = ctx2.createMediaStreamSource(out); const d2 = ctx2.createMediaStreamDestination(); s2.connect(gate); gate.connect(d2); out = d2.stream; window.__gctx = ctx2;
    }
  }
  const ctx = new AudioContext({sampleRate: 48000}); await ctx.resume();
  const src = ctx.createMediaStreamSource(out); const proc = ctx.createScriptProcessor(2048, 1, 1);
  const o = []; proc.onaudioprocess = (e) => { const d = e.inputBuffer.getChannelData(0); let s = 0, pk = 0; for (let i = 0; i < d.length; i++) { s += d[i] * d[i]; const a = Math.abs(d[i]); if (a > pk) pk = a; } o.push([s / d.length, pk]); };
  const g = ctx.createGain(); g.gain.value = 0; src.connect(proc); proc.connect(g); g.connect(ctx.destination);
  window.__pm = {o, sr: ctx.sampleRate, ctx, tr}; return {ns: st.noiseSuppression, ec: st.echoCancellation, agc: st.autoGainControl};
})"""


async def run(names):
    wav = os.path.join(tempfile.mkdtemp(prefix="noise-"), "noise_corpus.wav")
    meta = C.build(wav)
    base = os.environ.get("BROWSER_FLAGS", "")
    os.environ["BROWSER_FLAGS"] = f"{base} --use-file-for-fake-audio-capture={wav}"
    A = S.Client("P", "root", "root-pass", 9751)
    await A.start()
    res = {}
    try:
        await A.goto("/", 2)
        for n in names:
            cfg = CONFIGS[n]
            await A.js("(()=>{ if (window.__proc) { try { window.__proc.destroy(); } catch(e){} window.__proc = null; } if (window.__pm) { try { window.__pm.tr.stop(); window.__pm.ctx.close(); } catch(e){} } })()")
            if os.environ.get("GATE_OPTS"): await A.js(f"window.__gateOpts = {os.environ['GATE_OPTS']}")
            st = await A.js(f"{PIPE}({json.dumps(cfg['c'])}, {json.dumps(cfg['p'])})")
            await asyncio.sleep(RECORD)
            d = await A.js("({sr: window.__pm.sr, out: window.__pm.o})")
            an, sc = analyse(d["out"][int(10 * 23):], d["sr"], meta)         # первые 10 с — прогрев
            res[n] = {"settings": st, "levels": an, "score": sc}
            print(f"[{n}] настройки дорожки {st}; контраст {sc:.1f} дБ", flush=True)
    finally:
        await A.close()
    print("\nшум                         " + "  ".join(f"{n:>16s}" for n in names))
    for kind in C.NOISES:
        row = []
        for n in names:
            r = res[n]["levels"].get(kind)
            row.append("нет данных".rjust(16) if not r else f"{r['noise_rms']:6.1f}/{r['speech_rms']:6.1f}".rjust(16))
        print(f"{C.NOISE_LABEL[kind]:27s} " + "  ".join(row))
    print("(шум в паузах дБ / речь дБ; чем ниже первое при том же втором — тем лучше)")
    return res

def _main():
    if len(sys.argv) > 1 and sys.argv[1] == "latency":
        asyncio.run(latency())
    else:
        asyncio.run(run(sys.argv[1:] or list(CONFIGS)))


LAT = """(async (b64, gate) => {
  const bin = atob(b64); const pcm = new Int16Array(bin.length / 2); for (let i = 0; i < pcm.length; i++) pcm[i] = (bin.charCodeAt(2*i) | (bin.charCodeAt(2*i+1) << 8)) << 16 >> 16;
  const ctx = new AudioContext({sampleRate: 48000}); await ctx.resume();
  const buf = ctx.createBuffer(1, pcm.length, 48000); const ch = buf.getChannelData(0); for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const d0 = ctx.createMediaStreamDestination(); const t0 = d0.stream.getAudioTracks()[0];
  const mod = await import('/src/audio/rnnoise.ts'); const P = new mod.RnnoiseProcessor(); await P.init({track: t0, kind: 'audio'});
  const meter = (stream, out) => { const src = ctx.createMediaStreamSource(stream); const p = ctx.createScriptProcessor(256, 1, 1);
    p.onaudioprocess = (e) => { const d = e.inputBuffer.getChannelData(0); let s = 0; for (let i = 0; i < d.length; i++) s += d[i]*d[i]; out.push([ctx.currentTime, Math.sqrt(s / d.length)]); };
    const g = ctx.createGain(); g.gain.value = 0; src.connect(p); p.connect(g); g.connect(ctx.destination); };
  const A = [], B = []; meter(new MediaStream([t0]), A); meter(new MediaStream([P.processedTrack]), B);
  const s = ctx.createBufferSource(); s.buffer = buf; s.connect(d0); s.start(ctx.currentTime + 0.5);
  await new Promise(r => setTimeout(r, (pcm.length / 48000 + 2) * 1000));
  return {A, B, stage: P.stage};
})"""


async def latency():
    import base64
    sp = (C.load_speech()[: 48000 * 4] * 0.5 * 32767).astype(np.int16)
    A = S.Client("L", "root", "root-pass", 9752)
    await A.start()
    try:
        await A.goto("/", 2)
        r = await A.js(f"{LAT}({json.dumps(base64.b64encode(sp.tobytes()).decode())}, true)")
    finally:
        await A.close()
    def env(rows):
        t = np.array([x[0] for x in rows]); v = np.array([x[1] for x in rows]); return t, v
    ta, va = env(r["A"]); tb, vb = env(r["B"])
    grid = np.arange(0.5, 0.5 + 5.0, 0.001)
    ia, ib = np.interp(grid, ta, va), np.interp(grid, tb, vb)
    ia -= ia.mean(); ib -= ib.mean()
    best = max(range(0, 120), key=lambda k: float(np.dot(ia[: len(ia) - k], ib[k:])))
    print(f"задержка конвейера RNNoise + затвор относительно исходного потока: ≈ {best} мс (разрешение измерения ≈ 6 мс; стадия: {r['stage']})")
    return best


if __name__ == "__main__":
    _main()
