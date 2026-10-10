import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icons";
import { SPEEDS, clamp, fmtTime, keyAction, mediaErrorText, nextSpeed } from "../mediaPlayerMath";

export interface PlayerItem { id: string; meetingId: string; title: string; hasVideo: boolean; durationS: number; canDownload: boolean }

/**
 * Плеер записи встречи поверх страницы (без новой вкладки и без скачивания): воспроизведение и пауза, перемотка по шкале, время, громкость, скорость 0,75–2×, полный экран для видео,
 * клавиатура (пробел, ←/→, ↑/↓, M, F, «,» «.»), закрытие крестиком или Esc. Файл отдаёт сервер потоком с поддержкой Range, поэтому перемотка не загружает запись целиком.
 * При закрытии воспроизведение останавливается и соединение с файлом закрывается.
 */
export default function MediaPlayer({ item, onClose }: { item: PlayerItem; onClose: () => void }) {
  const media = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const src = `/api/v1/meetings/${item.meetingId}/media/${item.id}/stream`;
  const [playing, setPlaying] = useState(false);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(item.durationS || 0);
  const [vol, setVol] = useState(1);
  const [muted, setMuted] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [full, setFull] = useState(false);

  const toggle = useCallback(() => { const m = media.current; if (!m) return; if (m.paused) void m.play().catch(() => undefined); else m.pause(); }, []);
  const seekTo = useCallback((t: number) => { const m = media.current; if (m) m.currentTime = clamp(t, 0, Number.isFinite(m.duration) ? m.duration : t); }, []);
  const setSpeedTo = useCallback((s: number) => { setSpeed(s); if (media.current) media.current.playbackRate = s; }, []);
  const setVolume = useCallback((v: number) => { const x = clamp(v, 0, 1); setVol(x); setMuted(x === 0); if (media.current) { media.current.volume = x; media.current.muted = x === 0; } }, []);
  const toggleMute = useCallback(() => { setMuted((m) => { if (media.current) media.current.muted = !m; return !m; }); }, []);
  const toggleFull = useCallback(() => {
    if (!item.hasVideo) return;
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined); else void box.current?.requestFullscreen?.().catch(() => undefined);
  }, [item.hasVideo]);

  // клавиши: слушаем на окне плеера (фокус остаётся в нём); Esc — закрытие, если не в полноэкранном режиме (там Esc выходит из него)
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") { if (!document.fullscreenElement) { e.preventDefault(); onClose(); } return; }
      const t = e.target as HTMLElement;
      if (t.tagName === "SELECT" || (t.tagName === "INPUT" && (t as HTMLInputElement).type !== "range")) return;
      if ((e.key === " " || e.key === "Enter") && (t.tagName === "BUTTON" || t.tagName === "A")) return;      // кнопки сами реагируют на пробел и Enter
      const a = keyAction(e);
      if (!a) return;
      e.preventDefault();
      const m = media.current;
      if (a.type === "toggle") toggle();
      else if (a.type === "seek" && m) seekTo(m.currentTime + a.by);
      else if (a.type === "volume") setVolume((muted ? 0 : vol) + a.by);
      else if (a.type === "mute") toggleMute();
      else if (a.type === "fullscreen") toggleFull();
      else if (a.type === "speed") setSpeedTo(nextSpeed(speed, a.dir));
      else if (a.type === "home") seekTo(0);
      else if (a.type === "end" && m) seekTo(m.duration || 0);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [muted, vol, speed, onClose, toggle, seekTo, setVolume, toggleMute, toggleFull, setSpeedTo]);

  useEffect(() => { const f = () => setFull(!!document.fullscreenElement); document.addEventListener("fullscreenchange", f); return () => document.removeEventListener("fullscreenchange", f); }, []);
  useEffect(() => { box.current?.focus(); const prev = document.activeElement as HTMLElement | null; return () => prev?.focus?.(); }, []);
  // закрытие: остановить воспроизведение и закрыть соединение с файлом
  useEffect(() => () => { const m = media.current; if (m) { m.pause(); m.removeAttribute("src"); m.load(); } if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined); }, []);

  const onError = async () => {
    setLoading(false);
    try {                                   // браузер не показывает причину — спрашиваем сервер тем же запросом (первый байт) и переводим ответ в понятный текст
      const r = await fetch(src, { headers: { Range: "bytes=0-0" }, credentials: "same-origin" });
      if (r.ok) { setErr("Не удалось воспроизвести запись: формат не поддерживается этим браузером. Скачайте файл, если у вас есть разрешение."); return; }
      const d = await r.json().catch(() => null);
      setErr(mediaErrorText(r.status, typeof d?.detail === "object" ? d.detail?.message : d?.detail));
    } catch { setErr(mediaErrorText(0)); }
  };

  const shown = muted ? 0 : vol;
  const common = {
    ref: media as never, src, preload: "metadata" as const, onPlay: () => setPlaying(true), onPause: () => setPlaying(false), onError,
    onTimeUpdate: () => setCur(media.current?.currentTime ?? 0), onLoadedMetadata: () => { setLoading(false); const d = media.current?.duration; if (d && Number.isFinite(d)) setDur(d); },
    onDurationChange: () => { const d = media.current?.duration; if (d && Number.isFinite(d)) setDur(d); }, onWaiting: () => setLoading(true), onCanPlay: () => setLoading(false), onEnded: () => setPlaying(false),
  };
  return createPortal(
    <div className="mp-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`mp-win ${item.hasVideo ? "video" : "audio"} ${full ? "full" : ""}`} ref={box} role="dialog" aria-modal="true" aria-label={item.title} tabIndex={-1}>
        <div className="mp-head">
          <b className="mp-title" title={item.title}>{item.title}</b>
          {item.canDownload && <a className="btn mini" href={`${src}?download=true`} title="Скачать исходный файл" aria-label="Скачать исходный файл"><Icon name="download" size={15} /> Скачать</a>}
          <button type="button" className="btn mini ghost" onClick={onClose} aria-label="Закрыть плеер (Esc)" title="Закрыть (Esc)"><Icon name="close" size={16} /></button>
        </div>
        {item.hasVideo
          ? <div className="mp-stage" onDoubleClick={toggleFull}><video {...common} playsInline onClick={toggle} />{loading && !err && <span className="mp-spin" aria-hidden />}</div>
          : <div className="mp-audio"><Icon name="mic" size={36} /><span>{item.title}</span><audio {...common} />{loading && !err && <span className="mp-spin" aria-hidden />}</div>}
        {err && <div className="alert error mp-err" role="alert">{err}</div>}
        <div className="mp-bar">
          <input className="mp-seek" type="range" min={0} max={Math.max(dur, 1)} step={0.1} value={Math.min(cur, Math.max(dur, 1))} aria-label="Перемотка" aria-valuetext={`${fmtTime(cur)} из ${fmtTime(dur)}`}
                 onChange={(e) => seekTo(Number(e.target.value))} disabled={!!err} />
          <div className="mp-row">
            <button type="button" className="btn mini" onClick={toggle} disabled={!!err} aria-label={playing ? "Пауза" : "Воспроизвести"}><Icon name={playing ? "pause" : "play"} size={16} /></button>
            <span className="mp-time" aria-live="off">{fmtTime(cur)} / {fmtTime(dur)}</span>
            <span className="spacer" />
            <button type="button" className="btn mini ghost" onClick={toggleMute} aria-label={shown === 0 ? "Включить звук" : "Выключить звук"} aria-pressed={shown === 0}><Icon name={shown === 0 ? "volumeOff" : "volume"} size={16} /></button>
            <input className="mp-vol" type="range" min={0} max={1} step={0.05} value={shown} aria-label="Громкость" onChange={(e) => setVolume(Number(e.target.value))} />
            <select className="mp-speed" value={speed} aria-label="Скорость воспроизведения" onChange={(e) => setSpeedTo(Number(e.target.value))}>
              {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
            </select>
            {item.hasVideo && <button type="button" className="btn mini ghost" onClick={toggleFull} aria-label="Во весь экран (F)" title="Во весь экран (F)"><Icon name="expand" size={16} /></button>}
          </div>
          <div className="mp-hint muted small">Пробел — пауза · ←/→ — 5 с (Shift — 30 с) · ↑/↓ — громкость · M — звук · «,» «.» — скорость{item.hasVideo ? " · F — на весь экран" : ""} · Esc — закрыть</div>
        </div>
      </div>
    </div>, document.body);
}
