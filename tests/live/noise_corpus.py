"""Тестовый звук для проверки шумоподавления (см. scenario_noise_eval.py): речь из ресурсов проекта (asr-service/app/assets/selftest_ru.wav) + синтетические бытовые шумы.
Шумы: белый, клавиатура, мышь, вода, фоновые голоса (перевёрнутая речь — спектр речи без разборчивости), бытовой гул (вентилятор 100 Гц + удары).
Файл: 48 кГц, моно, 16 бит. Каждый сегмент: 3 с одного шума, 5 с речь + шум, 2 с шум. Нужен numpy."""
import os, wave
import numpy as np

SR = 48000
ROOT = os.path.dirname(os.path.abspath(__file__))
SPEECH = os.path.join(ROOT, "..", "..", "asr-service", "app", "assets", "selftest_ru.wav")
NOISES = ["white", "keyboard", "mouse", "water", "babble", "household"]
NOISE_LABEL = {"white": "белый шум", "keyboard": "клавиатура", "mouse": "мышь (щелчки)", "water": "шум воды", "babble": "голоса и крики на фоне", "household": "бытовой гул и удары"}
SPEECH_DBFS, SEG = -22.0, (3.0, 5.0, 2.0)
NOISE_DBFS = {"white": -36.0, "keyboard": -34.0, "mouse": -34.0, "water": -36.0, "babble": -34.0, "household": -36.0}


def _db(x): return 10 ** (x / 20)
def _rms(x): return float(np.sqrt(np.mean(np.square(x)) + 1e-12))
def _norm(x, dbfs): return x * (_db(dbfs) / _rms(x))


def load_speech() -> np.ndarray:
    with wave.open(SPEECH) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768
        sr = w.getframerate()
    t = np.arange(int(len(raw) * SR / sr)) / SR
    return np.interp(t, np.arange(len(raw)) / sr, raw)


def _lp(x, a): 
    y = np.empty_like(x); s = 0.0
    for i, v in enumerate(x): s += a * (v - s); y[i] = s
    return y


def make_noise(kind: str, n: int, speech: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if kind == "white":
        x = rng.standard_normal(n)
    elif kind == "keyboard":                               # щелчки по 15–40 мс, 5–9 в секунду, разной силы
        x = np.zeros(n)
        t = 0
        while t < n - 2000:
            L = int(SR * rng.uniform(0.012, 0.03)); b = rng.standard_normal(L) * np.exp(-np.arange(L) / (L / 4)); b -= _lp(b, 0.05)
            x[t:t + L] += b * rng.uniform(0.4, 1.0)
            t += int(SR * rng.uniform(0.08, 0.2))
    elif kind == "mouse":                                  # одиночные щелчки раз в 0,6–1,2 с
        x = np.zeros(n)
        t = int(SR * 0.3)
        while t < n - 2000:
            L = int(SR * 0.006); b = rng.standard_normal(L) * np.exp(-np.arange(L) / (L / 5))
            x[t:t + L] += b
            t += int(SR * rng.uniform(0.6, 1.2))
    elif kind == "water":                                  # розовый шум с «бульканьем»: медленная амплитудная модуляция
        w = rng.standard_normal(n); x = _lp(w, 0.2) + 0.5 * w * 0.3
        x *= 0.6 + 0.4 * np.sin(2 * np.pi * np.cumsum(rng.uniform(2, 9, n)) / SR)
    elif kind == "babble":                                 # четыре перевёрнутых копии речи с разными сдвигами и подъёмом высот
        x = np.zeros(n)
        for k in range(4):
            seg = speech[::-1] if k % 2 == 0 else speech[::-1][::1]
            seg = np.roll(seg, int(SR * (1.7 * k + 0.9)))
            reps = int(np.ceil(n / len(seg))); x += np.tile(seg, reps)[:n] * (1.0 - 0.15 * k)
    elif kind == "household":
        tt = np.arange(n) / SR
        fan = np.sin(2 * np.pi * 100 * tt) * 0.5 + np.sin(2 * np.pi * 200 * tt) * 0.25 + _lp(rng.standard_normal(n), 0.1) * 0.7
        x = fan.copy()
        t = int(SR * 0.8)
        while t < n - 8000:                                # удары: низкий «бум» каждые 1–2,5 с
            L = int(SR * 0.12); x[t:t + L] += np.sin(2 * np.pi * 70 * np.arange(L) / SR) * np.exp(-np.arange(L) / (L / 5)) * 4.0
            t += int(SR * rng.uniform(1.0, 2.5))
    else:
        raise ValueError(kind)
    return _norm(x, NOISE_DBFS[kind])


def build(path: str) -> dict:
    """Пишет файл и возвращает «маску»: для каждого сегмента — границы (в секундах от начала периода) и какой это шум; речь активна только в среднем отрезке."""
    rng = np.random.default_rng(20261019)
    sp_all = _norm(load_speech(), SPEECH_DBFS)
    parts, meta, t0 = [], [], 0.0
    for k, kind in enumerate(NOISES):
        a, b, c = (int(SR * s) for s in SEG)
        n = a + b + c
        noise = make_noise(kind, n, load_speech(), rng)
        sp = np.zeros(n)
        st = int(SR * (k * 3.0))
        sp[a:a + b] = sp_all[st:st + b] if st + b <= len(sp_all) else sp_all[:b]
        parts.append(np.clip(noise + sp, -1, 1))
        meta.append({"kind": kind, "noise_only": [[t0, t0 + SEG[0]], [t0 + SEG[0] + SEG[1], t0 + sum(SEG)]], "speech": [t0 + SEG[0], t0 + SEG[0] + SEG[1]]})
        t0 += sum(SEG)
    pcm = (np.concatenate(parts) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    return {"period": t0, "segments": meta}


if __name__ == "__main__":
    import json, sys
    print(json.dumps(build(sys.argv[1] if len(sys.argv) > 1 else "noise_corpus.wav"), ensure_ascii=False)[:400])
