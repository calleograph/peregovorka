/**
 * Диагностика со стороны зрителя: всё считается в его браузере из событий hls.js и элемента video и уходит на сервер только вместе с обычным пульсом страницы.
 * Нативный HLS (Safari, iPhone) не сообщает время загрузки сегментов и задержку: такие показатели остаются пустыми, а панель объясняет почему.
 */
export interface ClientStats {
  kbps?: number; est?: number; res?: string; fps?: number; frames?: number; drop?: number; buf?: number; lag?: number; lat?: number; seg_ms?: number; seg_kb?: number;
  stalls: number; stall_ms: number; errs: number; reconn: number; eng?: "hlsjs" | "native"; node?: string; aud?: 0 | 1; low?: 0 | 1;       // low: зритель на низком качестве (360p); aud: зритель сейчас на дорожке «только звук"
}
export interface ClientPoint { t: number; kbps?: number; buf?: number; lag?: number; seg_ms?: number }

type HlsLike = {
  on: (e: string, cb: (ev: string, d: never) => void) => void; latency?: number; bandwidthEstimate?: number; playingDate?: Date | null;
};
interface FragData { frag: { duration: number; type?: string; stats?: { loaded: number; loading: { start: number; end: number } } }; part?: unknown }

const HISTORY = 150;

export class ViewerStats {
  s: ClientStats = { stalls: 0, stall_ms: 0, errs: 0, reconn: 0 };
  history: ClientPoint[] = [];
  skew = 0;                                 // поправка часов зрителя по времени сервера
  node = "";
  onProblem: (() => void) | null = null;
  private hls: HlsLike | null = null;
  private el: HTMLVideoElement | null = null;
  private frags: { at: number; ms: number; bytes: number; dur: number }[] = [];
  private prev = { t: 0, frames: 0 };
  private stallAt = 0;
  private started = false;

  attachHls(hls: HlsLike, events: { FRAG_LOADED: string; ERROR: string }): void {
    this.hls = hls;
    this.s.eng = "hlsjs";
    hls.on(events.FRAG_LOADED, (_e, d: FragData) => {
      const st = d.frag.stats;
      if (!st || d.part || (d.frag.type && d.frag.type !== "main")) return;
      this.frags.push({ at: performance.now(), ms: Math.max(0, st.loading.end - st.loading.start), bytes: st.loaded, dur: d.frag.duration });
      if (this.frags.length > 8) this.frags.shift();
    });
    hls.on(events.ERROR, (_e, d: { fatal?: boolean }) => { if (d.fatal) { this.s.errs += 1; this.onProblem?.(); } });
  }

  attachVideo(el: HTMLVideoElement, native: boolean): () => void {
    this.el = el;
    if (native) this.s.eng = "native";
    const waiting = () => { if (!this.started || el.paused || el.seeking) return; if (!this.stallAt) { this.stallAt = performance.now(); this.s.stalls += 1; this.onProblem?.(); } };
    const playing = () => {
      this.started = true;
      if (this.stallAt) { this.s.stall_ms += performance.now() - this.stallAt; this.stallAt = 0; }
    };
    const error = () => { this.s.errs += 1; this.onProblem?.(); };
    el.addEventListener("waiting", waiting); el.addEventListener("playing", playing); el.addEventListener("error", error);
    return () => { el.removeEventListener("waiting", waiting); el.removeEventListener("playing", playing); el.removeEventListener("error", error); };
  }

  reconnect(): void { this.s.reconn += 1; this.onProblem?.(); }

  /** Снимок сейчас (раз в секунду, пока открыта панель, и перед каждым пульсом). */
  tick(): ClientStats {
    const s = this.s, el = this.el, now = performance.now();
    s.node = this.node || undefined;
    if (el) {
      if (el.videoWidth) s.res = `${el.videoWidth}x${el.videoHeight}`;
      const q = el.getVideoPlaybackQuality?.();
      if (q) {
        s.frames = q.totalVideoFrames; s.drop = q.droppedVideoFrames;
        const dt = (now - this.prev.t) / 1000;
        if (this.prev.t && dt > 0.5) { const f = (q.totalVideoFrames - this.prev.frames) / dt; s.fps = Math.round(f * 10) / 10; }
        if (!this.prev.t || dt > 0.5) this.prev = { t: now, frames: q.totalVideoFrames };
      }
      let buf = 0;
      for (let i = 0; i < el.buffered.length; i++) if (el.buffered.start(i) <= el.currentTime + 0.1 && el.buffered.end(i) >= el.currentTime) buf = el.buffered.end(i) - el.currentTime;
      s.buf = Math.round(buf * 100) / 100;
      const end = el.seekable.length ? el.seekable.end(el.seekable.length - 1) : 0;
      const lag = this.hls?.latency ?? (end ? end - el.currentTime : undefined);
      s.lag = lag === undefined || !Number.isFinite(lag) ? undefined : Math.round(Math.max(0, lag) * 100) / 100;
    }
    const played = this.hls?.playingDate;
    s.lat = played ? Math.max(0, Math.round((Date.now() + this.skew - played.getTime()) / 100) / 10) : undefined;
    const est = this.hls?.bandwidthEstimate;
    s.est = est && Number.isFinite(est) ? Math.round(est / 1000) : undefined;
    const recent = this.frags.filter((f) => now - f.at < 20_000);
    if (recent.length) {
      const dur = recent.reduce((a, f) => a + f.dur, 0), bytes = recent.reduce((a, f) => a + f.bytes, 0);
      s.seg_ms = Math.round(recent.reduce((a, f) => a + f.ms, 0) / recent.length);
      s.seg_kb = Math.round(bytes / recent.length / 1024);
      s.kbps = dur > 0 ? Math.round(bytes * 8 / dur / 1000) : undefined;
    } else { s.seg_ms = undefined; s.seg_kb = undefined; s.kbps = undefined; }
    const stall = this.stallAt ? now - this.stallAt : 0;
    const out = { ...s, stall_ms: Math.round(s.stall_ms + stall) };
    const last = this.history[this.history.length - 1];
    if (!last || Date.now() - last.t >= 1900) {
      this.history.push({ t: Date.now(), kbps: s.kbps, buf: s.buf, lag: s.lag, seg_ms: s.seg_ms });
      if (this.history.length > HISTORY) this.history.shift();
    }
    return out;
  }
}

export interface ClientVerdict { level: 0 | 1 | 2; text: string }

/** Что видно только из браузера: отставание сети от потока, потеря буфера. Сервера зритель не видит: вывод касается только его просмотра. */
export function clientVerdict(s: ClientStats, prevStalls: number): ClientVerdict {
  const segSlow = s.seg_ms !== undefined && s.seg_ms > 1200;
  const thin = s.est !== undefined && s.kbps !== undefined && s.est < s.kbps * 1.1;
  if (s.stalls - prevStalls > 0 || (s.buf !== undefined && s.buf < 0.3 && s.seg_ms !== undefined)) return { level: 2, text: segSlow || thin ? "Буфер пустеет: канал не успевает за потоком" : "Буфер пустеет: новые данные запаздывают" };
  if (segSlow || thin) return { level: 1, text: "Канал на пределе: сегменты загружаются почти так же долго, как играются" };
  if (s.lag !== undefined && s.lag > 12) return { level: 1, text: "Вы отстаёте от прямого эфира" };
  return { level: 0, text: "Просмотр идёт нормально" };
}
