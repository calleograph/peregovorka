"""Замер задержки, разрешения и синхронизации звука и изображения трансляции по этапам (настоящие LiveKit, Egress, nginx).

Выступающий (LiveKit Python SDK) показывает «экран», в каждом кадре которого закодировано время отправки (мс): 40 вертикальных полос на всю высоту
кадра — 36 бит времени, 1 бит вспышки, 3 бита контрольной суммы; «1» — светлая полоса, «0» — серая, фон сцены Egress тёмный, поэтому
полосы находятся в любом месте кадра. Каждые 5 с одновременно: бит вспышки в кадре и тон 1 кГц 150 мс в звуке.

  publish --url ws://… --token … --size 2560x1440 --seconds 600           выступающий
  hls --playlist URL --seconds 60                                         сегменты HLS: задержка готовности сегмента, разрешение, сдвиг звука
  rtmp --url rtmp://… --seconds 30                                        выход RTMP (приёмник): задержка, разрешение
Плеер (в браузере) декодирует те же полосы скриптом JS_DECODER (см. ниже) — задержка, которую видит зритель.
Все замеры — на одном сервере (часы общие). Синтетические данные."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import struct
import subprocess
import sys
import time
import urllib.request

BITS, DATA = 40, 36
TONE_EVERY_MS, TONE_MS = 5000, 150


def stat(xs: list[float]) -> dict | None:
    """Среднее, p50, p95, максимум, мс."""
    if not xs:
        return None
    v = sorted(xs)
    return {"n": len(v), "среднее": round(statistics.mean(v)), "p50": round(v[len(v) // 2]), "p95": round(v[int(len(v) * 0.95)]), "макс": round(v[-1])}


def encode(ms: int, flash: bool) -> list[int]:
    v = ms % (1 << DATA)
    bits = [(v >> (DATA - 1 - i)) & 1 for i in range(DATA)] + [1 if flash else 0]
    s = sum(bits) % 8
    return bits + [(s >> 2) & 1, (s >> 1) & 1, s & 1]


def decode_row(row: bytes) -> tuple[int, bool] | None:
    """Строка яркости через середину кадра → (мс, вспышка) или None (кадр на переходе, полос нет)."""
    lit = [i for i, y in enumerate(row) if y > 90]
    if len(lit) < BITS * 2:
        return None
    x0, x1 = lit[0], lit[-1] + 1
    w = (x1 - x0) / BITS
    bits = [1 if row[int(x0 + (i + 0.5) * w)] > 185 else 0 for i in range(BITS)]
    if sum(bits[:DATA + 1]) % 8 != (bits[37] << 2 | bits[38] << 1 | bits[39]):
        return None
    v = 0
    for b in bits[:DATA]:
        v = v << 1 | b
    now = int(time.time() * 1000)
    ms = now - ((now - v) % (1 << DATA))            # восстановить старшие разряды по текущему времени
    return ms, bool(bits[DATA])


JS_DECODER = r"""(() => { const v = document.querySelector('.live-stage video'); if (!v || !v.videoWidth) return null;
  const c = window.__pc || (window.__pc = document.createElement('canvas')); c.width = v.videoWidth; c.height = 1;
  const g = c.getContext('2d', { willReadFrequently: true }); g.drawImage(v, 0, v.videoHeight / 2, v.videoWidth, 1, 0, 0, v.videoWidth, 1);
  const d = g.getImageData(0, 0, v.videoWidth, 1).data, y = []; for (let i = 0; i < d.length; i += 4) y.push(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
  const lit = []; y.forEach((l, i) => { if (l > 90) lit.push(i); }); if (lit.length < 80) return { w: v.videoWidth, h: v.videoHeight, ms: null };
  const x0 = lit[0], x1 = lit[lit.length - 1] + 1, w = (x1 - x0) / 40, b = []; for (let i = 0; i < 40; i++) b.push(y[Math.floor(x0 + (i + 0.5) * w)] > 185 ? 1 : 0);
  let s = 0; for (let i = 0; i < 37; i++) s += b[i]; if (s % 8 !== (b[37] << 2 | b[38] << 1 | b[39])) return { w: v.videoWidth, h: v.videoHeight, ms: null };
  let val = 0; for (let i = 0; i < 36; i++) val = val * 2 + b[i]; const now = Date.now(), M = 2 ** 36;
  const ms = now - ((now - val) % M + M) % M; return { w: v.videoWidth, h: v.videoHeight, lag: now - ms, buffered: v.buffered.length ? v.buffered.end(v.buffered.length - 1) - v.currentTime : 0 }; })()"""


# ------------------------------------------------------------------------------------------------ выступающий
async def publish(a) -> None:
    from livekit import rtc

    w, h = (int(x) for x in a.size.split("x"))
    room = rtc.Room()
    await room.connect(a.url, a.token)
    src = rtc.VideoSource(w, h)
    await room.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("screen", src),
                                               rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE, video_encoding=rtc.VideoEncoding(max_bitrate=12_000_000, max_framerate=30)))
    asrc = rtc.AudioSource(48000, 1)
    await room.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    print("PUBLISHING", flush=True)
    sw = w // BITS
    hi, lo = bytes((235, 235, 235, 255)) * sw, bytes((128, 128, 128, 255)) * sw
    pad = bytes((128, 128, 128, 255)) * (w - sw * BITS)

    async def video():
        while True:
            t = time.time()
            ms = int(t * 1000)
            row = b"".join(hi if b else lo for b in encode(ms, ms % TONE_EVERY_MS < 200)) + pad
            src.capture_frame(rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, row * h))
            await asyncio.sleep(max(0, 1 / 30 - (time.time() - t)))

    async def audio():
        n = 0
        t0 = time.time()
        while True:
            start_ms = int((t0 + n * 0.01) * 1000)
            pcm = bytearray()
            for i in range(480):
                tms = start_ms + i / 48
                on = (tms % TONE_EVERY_MS) < TONE_MS
                pcm += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 1000 * i / 48000)) if on else 0)
            await asrc.capture_frame(rtc.AudioFrame(bytes(pcm), 48000, 1, 480))
            n += 1

    tasks = [asyncio.create_task(video()), asyncio.create_task(audio())]
    await asyncio.sleep(a.seconds)
    for t in tasks:
        t.cancel()
    await room.disconnect()


# ------------------------------------------------------------------------------------------------ разбор видео
def probe_size(src: str) -> str:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name,profile,bit_rate", "-of", "json", src],
                       capture_output=True, text=True, timeout=30)
    s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
    return f"{s.get('codec_name')} {s.get('profile')} {s.get('width')}x{s.get('height')}"


def rows(src: str, extra: list[str] | None = None, timeout: float = 30) -> list[tuple[float, tuple[int, bool] | None]]:
    """(pts, декодированное) для каждого кадра источника."""
    w, h = (int(x) for x in re.findall(r"(\d+)x(\d+)", probe_size(src))[0])
    p = subprocess.run(["ffmpeg", "-v", "info", *(extra or []), "-i", src, "-vf", f"crop={w}:2:0:{h // 2},format=gray,showinfo", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=timeout)      # высота 2: у видео 4:2:0 строка в 1 пиксель не вырезается
    vst = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time", "-of", "csv=p=0", src], capture_output=True, text=True).stdout.split()
    v0 = float(vst[0]) if vst and vst[0] not in ("N/A", "") else 0.0      # абсолютное время потока: так же считается звук (audio_onsets)
    pts = [v0 + float(x) for x in re.findall(rb"pts_time:([0-9.]+)", p.stderr)]
    data = p.stdout
    return [(pts[i] if i < len(pts) else 0.0, decode_row(data[i * 2 * w:i * 2 * w + w])) for i in range(len(data) // (2 * w))]


def audio_onsets(src: str) -> list[float]:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-"], capture_output=True, timeout=30)
    st = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time", "-of", "csv=p=0", src], capture_output=True, text=True).stdout.strip()
    start = float((st.split() or ["0"])[0])
    samples = struct.unpack(f"<{len(p.stdout) // 2}h", p.stdout[: len(p.stdout) // 2 * 2])
    out, quiet = [], 0
    for i, s in enumerate(samples):
        if abs(s) > 4000:
            if quiet > 4800:                 # тишина не меньше 100 мс перед сигналом
                out.append(start + i / 48000)
            quiet = 0
        else:
            quiet += 1
    return out


def hls(a) -> None:
    seen, lags, sync, sizes, segdur = set(), [], [], set(), []
    base = a.playlist.rsplit("/", 1)[0] + "/"
    first, end = True, time.time() + a.seconds
    while time.time() < end:
        t_req = time.time()
        try:
            pl = urllib.request.urlopen(a.playlist, timeout=10).read().decode()
        except Exception as e:  # noqa: BLE001
            print("плейлист:", e); time.sleep(0.5); continue
        for dur, name in re.findall(r"#EXTINF:([0-9.]+),\s*\n([^\n#]+)", pl):
            name = name.strip()
            if name in seen:
                continue
            seen.add(name)
            if first:
                continue                                  # уже были в первом плейлисте — не свежие
            path = f"{a.tmp}/seg.ts"
            open(path, "wb").write(urllib.request.urlopen(base + name, timeout=10).read())
            frames = [f for f in rows(path) if f[1]]
            sizes.add(probe_size(path))
            segdur.append(float(dur))
            if not frames:
                continue
            lags.append(t_req * 1000 - frames[-1][1][0])  # сегмент появился в плейлисте: столько прошло с момента отправки его последнего кадра
            flash = [f for f in frames if f[1][1]]
            ons = audio_onsets(path)
            if flash and ons:
                fp = flash[0][0]
                near = min(ons, key=lambda x: abs(x - fp))
                if abs(near - fp) < 1.0:
                    sync.append((near - fp) * 1000)
        first = False
        time.sleep(0.1)
    rep = {"сегменты": len(segdur), "длительность_сегмента_с": round(statistics.mean(segdur), 2) if segdur else None, "разрешение": sorted(sizes),
           "готовность_сегмента_мс": stat(lags),
           "звук_минус_изображение_мс": [round(x) for x in sync]}
    print(json.dumps(rep, ensure_ascii=False))


def rtmp(a) -> None:
    size = probe_size(a.url)
    w, h = (int(x) for x in re.findall(r"(\d+)x(\d+)", size)[0])
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-fflags", "nobuffer", "-flags", "low_delay", "-i", a.url, "-vf", f"crop={w}:2:0:{h // 2},format=gray", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE)
    lags, end = [], time.time() + a.seconds
    while time.time() < end:
        row = p.stdout.read(2 * w)[:w]
        if len(row) < w:
            break
        d = decode_row(row)
        if d:
            lags.append(time.time() * 1000 - d[0])
    p.kill()
    lags = lags[len(lags) // 5:]                       # начало чтения — догоняет накопленное
    print(json.dumps({"разрешение": size, "задержка_мс": stat(lags)}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("publish"); p1.add_argument("--url"); p1.add_argument("--token"); p1.add_argument("--size", default="2560x1440"); p1.add_argument("--seconds", type=float, default=600)
    p2 = sub.add_parser("hls"); p2.add_argument("--playlist"); p2.add_argument("--seconds", type=float, default=60); p2.add_argument("--tmp", default="/tmp")
    p3 = sub.add_parser("rtmp"); p3.add_argument("--url"); p3.add_argument("--seconds", type=float, default=30)
    a = ap.parse_args()
    {"publish": lambda: asyncio.run(publish(a)), "hls": lambda: hls(a), "rtmp": lambda: rtmp(a)}[a.cmd]()
    sys.exit(0)
