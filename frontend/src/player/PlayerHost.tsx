import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, type SubtitleData } from "../api";
import { Icon } from "../components/Icons";
import { SPEEDS, clamp, fmtTime, keyAction, mediaErrorText, nextSpeed } from "../mediaPlayerMath";
import { MIN_FULL, activeCues, clampRect, decodePeaks, defaultRect, parsePrefs, type Cue, type Prefs, type Rect } from "./playerMath";
import { closePlayer, setPlayerMode, usePlayer, type PlayerItem } from "./store";
import TranscriptPanel from "./TranscriptPanel";
import Waveform from "./Waveform";

const LS_PREFS = "pg:player:prefs", LS_FULL = "pg:player:rect:full", LS_MINI = "pg:player:rect:mini";
const store = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* хранилище недоступно — настройки не запоминаются */ } },
};
const vp = () => ({ w: window.innerWidth, h: window.innerHeight });
function loadRect(key: string, mode: "full" | "mini"): Rect {
  const { w, h } = vp();
  try {
    const o = JSON.parse(store.get(key) ?? "null");
    if (o && ["x", "y", "w", "h"].every((k) => typeof o[k] === "number")) return clampRect({ x: o.x, y: o.y, w: o.w, h: o.h }, w, h);
  } catch { /* повреждённое значение */ }
  return defaultRect(w, h, mode);
}
function useMedia(q: string): boolean {
  const [m, setM] = useState(() => window.matchMedia(q).matches);
  useEffect(() => { const mq = window.matchMedia(q); const f = () => setM(mq.matches); mq.addEventListener("change", f); return () => mq.removeEventListener("change", f); }, [q]);
  return m;
}

/**
 * Плавающий плеер записей. Живёт на уровне приложения (не страницы истории): окно без затемнения и блокировки, перетаскивается за заголовок, меняет размер, сворачивается в мини-плеер;
 * звук не прерывается ни при смене раздела, ни при переключении «полный ↔ мини», ни при перетаскивании — медиа-элемент один и не пересоздаётся. Открытие другой записи заменяет текущую.
 */
export default function PlayerHost() {
  const { item, mode, focusTick } = usePlayer();
  if (!item) return null;
  return <PlayerWindow key={item.id} item={item} mode={mode} focusTick={focusTick} />;
}

function PlayerWindow({ item, mode, focusTick }: { item: PlayerItem; mode: "full" | "mini"; focusTick: number }) {
  const media = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const win = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const mobile = useMedia("(max-width: 640px)");
  const { pathname } = useLocation();
  const src = `/api/v1/meetings/${item.meetingId}/media/${item.id}/stream`;

  const [prefs, setPrefsState] = useState<Prefs>(() => parsePrefs(store.get(LS_PREFS)));
  const setPrefs = useCallback((p: Partial<Prefs>) => setPrefsState((cur) => { const n = { ...cur, ...p }; store.set(LS_PREFS, JSON.stringify(n)); return n; }), []);
  const [full, setFull] = useState<Rect>(() => loadRect(LS_FULL, "full"));
  const [mini, setMini] = useState<Rect>(() => loadRect(LS_MINI, "mini"));
  const rect = mode === "full" ? full : mini;

  const [playing, setPlaying] = useState(false);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(item.durationS || 0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [peaks, setPeaks] = useState<Uint8Array | null>(null);
  const [subs, setSubs] = useState<SubtitleData | null>(null);
  const [fs, setFs] = useState(false);

  // --- данные: волна (строится на сервере один раз; пока нет — обычная шкала) и субтитры (уже имеющиеся реплики стенограммы)
  useEffect(() => {
    let alive = true; let timer = 0;
    const go = async (n: number) => {
      try {
        const w = await api.mediaWaveform(item.meetingId, item.id);
        if (!alive) return;
        if (w.status === "ready" && w.peaks) setPeaks(decodePeaks(w.peaks));
        else if (w.status === "processing" && n < 80) timer = window.setTimeout(() => void go(n + 1), 2500);
      } catch { /* нет волны — остаётся обычная шкала */ }
    };
    void go(0);
    api.mediaSubtitles(item.meetingId, item.id).then((s) => { if (alive) setSubs(s); }).catch(() => { if (alive) setSubs({ available: false, reason: "error", message: "Субтитры недоступны: не удалось загрузить стенограмму.", scope: "meeting", segments: [] }); });
    return () => { alive = false; window.clearTimeout(timer); };
  }, [item.id, item.meetingId]);
  const cues: Cue[] = useMemo(() => subs?.segments ?? [], [subs]);

  // --- состояние воспроизведения; текущая позиция обновляется плавно (rAF), пока играет
  useEffect(() => {
    if (!playing) return;
    let raf = 0; let last = 0;
    const tick = (now: number) => { if (now - last > 50) { last = now; setCur(media.current?.currentTime ?? 0); } raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);
  useEffect(() => { const m = media.current; if (!m) return; m.volume = prefs.volume; m.muted = prefs.muted; m.playbackRate = prefs.speed; }, [prefs.volume, prefs.muted, prefs.speed]);
  useEffect(() => () => { const m = media.current; if (m) { m.pause(); m.removeAttribute("src"); m.load(); } }, []);          // закрытие: остановить и закрыть соединение с файлом
  useEffect(() => { win.current?.focus(); }, [focusTick]);
  useEffect(() => { const f = () => setFs(!!document.fullscreenElement); document.addEventListener("fullscreenchange", f); return () => document.removeEventListener("fullscreenchange", f); }, []);
  // во время встречи запись не играет (иначе звук попадёт в микрофон): ставим на паузу и сворачиваем
  useEffect(() => { if (pathname.startsWith("/rooms/")) { media.current?.pause(); setPlayerMode("mini"); } }, [pathname]);
  useEffect(() => {          // окно остаётся в видимой области при изменении размера экрана
    const f = () => { const { w, h } = vp(); const fix = (r: Rect) => (r.auto ? r : clampRect(r, w, h)); setFull(fix); setMini(fix); };
    window.addEventListener("resize", f); return () => window.removeEventListener("resize", f);
  }, []);

  const toggle = useCallback(() => { const m = media.current; if (!m) return; if (m.paused) void m.play().catch(() => undefined); else m.pause(); }, []);
  const seek = useCallback((t: number) => { const m = media.current; if (!m) return; const d = Number.isFinite(m.duration) ? m.duration : dur; m.currentTime = clamp(t, 0, d || t); setCur(m.currentTime); }, [dur]);
  const toggleFull = () => { if (!item.hasVideo) return; if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined); else void stage.current?.requestFullscreen?.().catch(() => undefined); };

  const onError = async () => {
    setLoading(false);
    try {          // браузер не сообщает причину — спрашиваем сервер тем же запросом и переводим ответ в понятный текст
      const r = await fetch(src, { headers: { Range: "bytes=0-0" }, credentials: "same-origin" });
      if (r.ok) { setErr("Не удалось воспроизвести запись: формат не поддерживается этим браузером."); return; }
      const d = await r.json().catch(() => null);
      setErr(mediaErrorText(r.status, typeof d?.detail === "object" ? d.detail?.message : d?.detail));
    } catch { setErr(mediaErrorText(0)); }
  };

  // --- клавиатура: только когда фокус в окне плеера (в остальном интерфейсе пробел и стрелки принадлежат странице); Esc закрывает, только если фокус в плеере
  const onKeyDown = (e: React.KeyboardEvent) => {
    const t = e.target as HTMLElement;
    if (e.key === "Escape") { if (document.fullscreenElement) return; e.preventDefault(); e.stopPropagation(); closePlayer(); return; }
    if (t.tagName === "SELECT" || t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && (t as HTMLInputElement).type !== "range")) return;
    if ((e.key === " " || e.key === "Enter") && (t.tagName === "BUTTON" || t.tagName === "A")) return;
    const a = keyAction(e.nativeEvent);
    if (!a) return;
    e.preventDefault();
    const m = media.current;
    if (a.type === "toggle") toggle();
    else if (a.type === "seek" && m) seek(m.currentTime + a.by);
    else if (a.type === "volume") setPrefs({ volume: clamp((prefs.muted ? 0 : prefs.volume) + a.by, 0, 1), muted: false });
    else if (a.type === "mute") setPrefs({ muted: !prefs.muted });
    else if (a.type === "fullscreen") toggleFull();
    else if (a.type === "speed") setPrefs({ speed: nextSpeed(prefs.speed, a.dir) });
    else if (a.type === "home") seek(0);
    else if (a.type === "end" && m) seek(m.duration || 0);
  };

  // --- перетаскивание за заголовок и изменение размера
  const setRect = (r: Rect) => (mode === "full" ? setFull(r) : setMini(r));
  const drag = useRef<{ dx: number; dy: number } | null>(null);
  const startDrag = (e: React.PointerEvent) => {
    if (mobile || fs || (e.target as HTMLElement).closest("button, a, input, select")) return;
    let r = rect;
    if (r.auto) { const b = win.current!.getBoundingClientRect(); r = { x: b.left, y: b.top, w: b.width, h: b.height }; setRect(r); }     // первое перетаскивание: из «угла» в явные координаты
    drag.current = { dx: e.clientX - r.x, dy: e.clientY - r.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const moveDrag = (e: React.PointerEvent) => { if (drag.current) { const { w, h } = vp(); setRect(clampRect({ ...rect, auto: false, x: e.clientX - drag.current.dx, y: e.clientY - drag.current.dy }, w, h)); } };
  const endDrag = (e: React.PointerEvent) => { if (!drag.current) return; drag.current = null; try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* уже отпущен */ } if (!rect.auto) store.set(mode === "full" ? LS_FULL : LS_MINI, JSON.stringify(rect)); };
  const size = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const startSize = (e: React.PointerEvent) => {
    if (full.auto) { const b = win.current!.getBoundingClientRect(); setFull({ x: b.left, y: b.top, w: b.width, h: b.height }); }
    size.current = { x: e.clientX, y: e.clientY, w: rect.w, h: rect.h }; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); e.stopPropagation(); };
  const moveSize = (e: React.PointerEvent) => {
    if (!size.current) return;
    const { w, h } = vp();
    setFull((f) => clampRect({ ...f, auto: false, w: Math.max(MIN_FULL.w, size.current!.w + e.clientX - size.current!.x), h: Math.max(MIN_FULL.h, size.current!.h + e.clientY - size.current!.y) }, w, h));
  };
  const endSize = (e: React.PointerEvent) => { if (!size.current) return; size.current = null; try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* */ } store.set(LS_FULL, JSON.stringify({ ...full, auto: undefined })); };

  const toggleTranscript = () => {
    const on = !prefs.transcript;
    setPrefs({ transcript: on });
    if (on && !mobile && full.w < 760) {
      const { w, h } = vp(); const nw = Math.min(820, w - 24);
      const b = win.current!.getBoundingClientRect();
      const nh = Math.min(Math.max(full.h, 420), h - 24);
      const r = clampRect({ x: Math.min(b.left, w - nw - 12), y: Math.min(b.top, h - nh - 16), w: nw, h: nh }, w, h);
      setFull(r); store.set(LS_FULL, JSON.stringify(r));
    }
  };

  const shown = prefs.muted ? 0 : prefs.volume;
  const live = activeCues(cues, cur);
  const subLine = prefs.cc && (
    subs && !subs.available ? <div className="plr-subs off" role="status">{subs.message}</div>
      : <div className="plr-subs" aria-live="off">{live.map((c) => <div key={c.id}>{subs?.scope === "meeting" && <b>{c.speaker}: </b>}{c.text}</div>)}</div>
  );
  const common = {
    ref: media as never, src, preload: "metadata" as const, onPlay: () => setPlaying(true), onPause: () => setPlaying(false), onError, playsInline: true,
    onTimeUpdate: () => { if (!playing) setCur(media.current?.currentTime ?? 0); },
    onLoadedMetadata: () => { setLoading(false); const m = media.current; if (m) { m.volume = prefs.volume; m.muted = prefs.muted; m.playbackRate = prefs.speed; if (Number.isFinite(m.duration)) setDur(m.duration); } },
    onDurationChange: () => { const d = media.current?.duration; if (d && Number.isFinite(d)) setDur(d); },
    onWaiting: () => setLoading(true), onCanPlay: () => setLoading(false), onEnded: () => setPlaying(false),
  };
  const isMini = mode === "mini";
  const split = !isMini && prefs.transcript && !mobile && rect.w >= 700;
  const style = mobile ? undefined : rect.auto ? { right: 16, bottom: 16, width: rect.w, height: rect.h } : { left: rect.x, top: rect.y, width: rect.w, height: rect.h };
  const trUnavailable = subs && !subs.available ? subs.message : null;

  return (
    <div ref={win} className={`plr ${isMini ? "mini" : "full"} ${mobile ? "mobile" : ""} ${item.hasVideo ? "video" : "audio"} ${split ? "split" : ""} ${fs ? "fs" : ""}`} style={style} tabIndex={-1} role="dialog"
         aria-label={`Плеер: ${item.title}`} onKeyDown={onKeyDown}>
      <div className="plr-head" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag} onDoubleClick={(e) => { if (!(e.target as HTMLElement).closest("button, a")) setPlayerMode(isMini ? "full" : "mini"); }}>
        {isMini && <button type="button" className="btn mini" onClick={toggle} disabled={!!err} aria-label={playing ? "Пауза" : "Воспроизвести"}><Icon name={playing ? "pause" : "play"} size={16} /></button>}
        <div className="plr-title"><b title={item.title}>{item.title}</b>{item.subtitle && !isMini && <span className="muted small"> · {item.subtitle}</span>}{isMini && <span className="plr-mtime">{fmtTime(cur)} / {fmtTime(dur)}</span>}</div>
        {!isMini && item.canDownload && <a className="btn mini" href={`${src}?download=true`} title="Скачать исходный файл" aria-label="Скачать исходный файл"><Icon name="download" size={15} /></a>}
        {isMini
          ? <button type="button" className="btn mini ghost" onClick={() => setPlayerMode("full")} aria-label="Развернуть плеер" title="Развернуть"><Icon name="expand" size={15} /></button>
          : <button type="button" className="btn mini ghost" onClick={() => setPlayerMode("mini")} aria-label="Свернуть в мини-плеер" title="Свернуть в мини-плеер"><Icon name="mini" size={15} /></button>}
        <button type="button" className="btn mini ghost" onClick={closePlayer} aria-label="Закрыть плеер (Esc)" title="Закрыть (Esc)"><Icon name="close" size={15} /></button>
      </div>

      <div className="plr-main">
        <div className="plr-left">
          {item.hasVideo && <div className="plr-stage" ref={stage} onDoubleClick={toggleFull}><video {...common} onClick={toggle} />{!isMini && prefs.cc && <div className="plr-vsubs">{subLine}</div>}{loading && !err && !isMini && <span className="plr-spin" aria-hidden />}</div>}
          {!item.hasVideo && <audio {...common} />}
          {!isMini && !item.hasVideo && <div className="plr-sub-area">{prefs.cc ? subLine : null}</div>}
          {err && <div className="alert error plr-err" role="alert">{err}</div>}
          <div className="plr-wave"><Waveform peaks={peaks} duration={dur} current={cur} onSeek={seek} height={isMini ? 30 : 88} />{loading && !err && !isMini && !item.hasVideo && <span className="plr-spin" aria-hidden />}</div>
          {!isMini && (
            <div className="plr-ctl">
              <button type="button" className="btn mini primary" onClick={toggle} disabled={!!err} aria-label={playing ? "Пауза" : "Воспроизвести"}><Icon name={playing ? "pause" : "play"} size={16} /></button>
              <span className="plr-time" aria-live="off">{fmtTime(cur)} / {fmtTime(dur)}</span>
              <span className="spacer" />
              <button type="button" className="btn mini ghost" onClick={() => setPrefs({ muted: !prefs.muted })} aria-label={shown === 0 ? "Включить звук" : "Выключить звук"} aria-pressed={shown === 0}><Icon name={shown === 0 ? "volumeOff" : "volume"} size={16} /></button>
              <input className="plr-vol" type="range" min={0} max={1} step={0.05} value={shown} aria-label="Громкость" onChange={(e) => setPrefs({ volume: Number(e.target.value), muted: Number(e.target.value) === 0 })} />
              <select className="plr-speed" value={prefs.speed} aria-label="Скорость воспроизведения" onChange={(e) => setPrefs({ speed: Number(e.target.value) })}>{SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}</select>
              <button type="button" className={`btn mini ${prefs.cc ? "primary" : "ghost"}`} onClick={() => setPrefs({ cc: !prefs.cc })} aria-pressed={prefs.cc} aria-label="Субтитры" title={subs && !subs.available ? subs.message ?? "Субтитры недоступны" : "Субтитры"}><Icon name="cc" size={16} /></button>
              <button type="button" className={`btn mini ${prefs.transcript ? "primary" : "ghost"}`} onClick={toggleTranscript} aria-pressed={prefs.transcript} aria-label="Стенограмма" title="Стенограмма рядом с записью"><Icon name="transcript" size={16} /></button>
              {item.hasVideo && <button type="button" className="btn mini ghost" onClick={toggleFull} aria-label="Во весь экран (F)" title="Во весь экран (F)"><Icon name="expand" size={16} /></button>}
            </div>
          )}
          {!isMini && <div className="plr-hint muted small">Пробел — пауза · ←/→ — 5 с (Shift — 30 с) · ↑/↓ — громкость · M — звук · «,» «.» — скорость · Esc — закрыть</div>}
        </div>
        {!isMini && prefs.transcript && <TranscriptPanel cues={cues} time={cur} follow={prefs.follow} onFollow={(v) => setPrefs({ follow: v })} onSeek={seek} unavailable={trUnavailable} scope={subs?.scope ?? "meeting"} />}
      </div>
      {!isMini && !mobile && !fs && <div className="plr-grip" onPointerDown={startSize} onPointerMove={moveSize} onPointerUp={endSize} onPointerCancel={endSize} aria-hidden title="Изменить размер" />}
    </div>
  );
}

