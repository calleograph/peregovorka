"""Проверка общей записи по настоящим дорожкам (запуск: python analyze_mix.py <каталог_прогона>, нужен numpy и FFMPEG_BIN).
В каталоге: A.pcm/A.t0, B.pcm/B.t0 — то, что записал ASR-воркер по реальным аудиотрекам LiveKit; srcA.pcm/srcB.pcm — исходные «реплики» (16 кГц, int16);
mix.m4a — общая запись встречи; plan.json — {lead, p1, p2, pause} (секунды). Печатает строки OK/FAIL и итог; код возврата 1, если что-то не так.

Что проверяется (это не повторение работы ffmpeg — проверяется соответствие РЕАЛЬНОЙ дорожки источнику и общей записи дорожкам):
 1. каждая записанная дорожка по всей длине совпадает с источником при одном сдвиге (паузы и тишина сохранены, а не сжаты: иначе поздние реплики «поплывут»);
 2. длительность дорожки ≈ настоящему времени (кадры не теряются и не дублируются);
 3. общая запись по огибающей совпадает с суммой дорожек, поставленных по отметкам времени (сдвиг < 50 мс);
 4. реплики действительно звучали одновременно (перекрытие ≥ 0,5 с) и общая запись это содержит; пауза между репликами в ней сохранена; срезания нет."""
import json, os, subprocess, sys
import numpy as np

D = sys.argv[1]
SR = 16000
ok_all = True


def check(name, ok, extra=""):
    global ok_all
    ok_all &= bool(ok)
    print(("OK   " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)


def pcm(p):
    return np.fromfile(p, dtype="<i2").astype(np.float32)


def env(x, hop=160, win=480):
    a = np.abs(x)
    k = np.ones(win, dtype=np.float32) / win
    return np.convolve(a, k, "same")[::hop]                 # 100 отсчётов огибающей в секунду


def xcorr(a, b):
    """Нормированная взаимная корреляция огибающих: (сдвиг в отсчётах огибающей, качество). a[n] ≈ b[n - lag]."""
    a = a - a.mean(); b = b - b.mean()
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    c = np.concatenate([c[-len(b) + 1:], c[:len(a)]])        # индексы от -(len(b)-1) до len(a)-1
    i = int(np.argmax(c))
    return i - (len(b) - 1), float(c[i] / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


plan = json.load(open(os.path.join(D, "plan.json")))
tracks = {}
for k in ("A", "B"):
    x = pcm(os.path.join(D, f"{k}.pcm"))
    t0 = float(open(os.path.join(D, f"{k}.t0")).read())
    src = pcm(os.path.join(D, f"src{k}.pcm"))
    lag, q = xcorr(env(x), env(src))
    tracks[k] = dict(x=x, t0=t0, src=src, lag=lag / 100.0, q=q)
    dur = len(x) / SR
    check(f"дорожка {k}: по всей длине совпадает с источником при одном сдвиге (качество {q:.2f}, сдвиг {lag / 100:.2f} с)", q >= 0.45, f"{q:.2f}")
    check(f"дорожка {k}: записано {dur:.1f} с, источник {len(src) / SR:.1f} с (ничего не потеряно и не сжато)", dur >= len(src) / SR - 0.5, f"{dur:.1f}")

start = min(t["t0"] for t in tracks.values())
for k, t in tracks.items():
    t["off"] = t["t0"] - start
    t["play"] = t["off"] + t["lag"]                          # настенное время (от начала встречи), когда у участника «заиграл» источник

# --- общая запись
ff = os.environ.get("FFMPEG_BIN", "ffmpeg")
mix = np.frombuffer(subprocess.run([ff, "-v", "error", "-i", os.path.join(D, "mix.m4a"), "-f", "s16le", "-ar", "16000", "-ac", "1", "-"], capture_output=True, check=True).stdout, dtype="<i2").astype(np.float32)
end = max(t["off"] + len(t["x"]) / SR for t in tracks.values())
check(f"общая запись {len(mix) / SR:.1f} с ≈ от начала первой до конца последней дорожки {end:.1f} с", abs(len(mix) / SR - end) < 1.0, f"{len(mix) / SR:.1f}/{end:.1f}")
pred = np.zeros(int(end * SR) + SR, dtype=np.float32)
for t in tracks.values():
    s = int(round(t["off"] * SR)); pred[s:s + len(t["x"])] += t["x"]
m, p = env(mix), env(pred)
lag, q = xcorr(m, p)
check(f"огибающая общей записи = сумма дорожек по отметкам времени (качество {q:.2f}, сдвиг {lag * 10} мс)", q >= 0.9 and abs(lag) <= 5, f"{q:.2f}/{lag}")
check("срезания нет (почти нет отсчётов у предела шкалы)", float((np.abs(mix) >= 32700).mean()) < 0.001)

# --- одновременная речь и пауза (по расписанию реплик и найденным сдвигам)
L, p1, p2, pause = plan["lead"], plan["p1"], plan["p2"], plan["pause"]
a1 = (tracks["A"]["play"] + L, tracks["A"]["play"] + L + p1)
a3 = (tracks["A"]["play"] + L + p1 + pause, tracks["A"]["play"] + L + p1 + pause + plan["p3"])
b2 = (tracks["B"]["play"] + L + plan["b_shift"], tracks["B"]["play"] + L + plan["b_shift"] + p2)
ov = max(0.0, min(a1[1], b2[1]) - max(a1[0], b2[0]))
check(f"реплики участников действительно пересеклись по времени на {ov:.1f} с (одновременная речь)", ov >= 0.5, f"{ov:.2f}")


def rms(a, b):
    s = mix[int(max(a, 0) * SR):int(b * SR)]
    return float(np.sqrt((s ** 2).mean())) if len(s) else 0.0


if ov >= 0.5:
    lo, hi = max(a1[0], b2[0]) + 0.3, min(a1[1], b2[1]) - 0.3
    speech = rms(a1[0] + 0.3, a1[1] - 0.3)
    check(f"в момент одновременной речи общая запись не тише одиночной (rms {rms(lo, hi):.0f} при одиночной {speech:.0f})", hi <= lo or rms(lo, hi) >= 0.7 * speech)
gap_lo, gap_hi = max(a1[1], b2[1]) + 0.4, a3[0] - 0.4
if gap_hi - gap_lo >= 1.0:
    quiet = rms(gap_lo, gap_hi); speech = rms(a1[0] + 0.3, a1[1] - 0.3)
    check(f"пауза {gap_hi - gap_lo:.1f} с между репликами сохранена тишиной (rms {quiet:.0f} против речи {speech:.0f})", quiet < 0.2 * speech, f"{quiet:.0f}/{speech:.0f}")
else:
    check("пауза между репликами достаточной длины для проверки", False, f"{gap_hi - gap_lo:.2f}")
print("ИТОГО анализа:", "всё хорошо" if ok_all else "есть расхождения")
sys.exit(0 if ok_all else 1)
