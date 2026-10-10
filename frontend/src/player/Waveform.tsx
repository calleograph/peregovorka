import { useCallback, useEffect, useRef, useState } from "react";
import { fmtTime } from "../mediaPlayerMath";
import { reducePeaks, timeAtX } from "./playerMath";

interface Props {
  peaks: Uint8Array | null;          // null — волна ещё строится (показываем обычную шкалу)
  duration: number;
  current: number;
  onSeek: (t: number) => void;
  height?: number;
  label?: string;
}

const BAR = 3, GAP = 1;

/**
 * Волновая форма: высота столбика — громкость на этом участке (по пикам с сервера), проигранная часть — цветом акцента, остальное — нейтральным; текущая позиция — линия.
 * Нажатие и перетаскивание мышью, пальцем или пером перематывают запись; при наведении показывается время. Пока волны нет — обычная шкала (та же перемотка).
 */
export default function Waveform({ peaks, duration, current, onSeek, height = 84, label = "Позиция воспроизведения" }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<{ x: number; t: number } | null>(null);
  const dragging = useRef(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(() => requestAnimationFrame(() => setWidth(el.clientWidth)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // столбики пересчитываются только при смене данных или ширины, а не на каждый кадр воспроизведения
  const bars = useRef<number[]>([]);
  useEffect(() => { bars.current = peaks && width > 0 ? reducePeaks(peaks, Math.floor(width / (BAR + GAP))) : []; }, [peaks, width]);

  const draw = useCallback(() => {
    const c = canvas.current;
    if (!c || width <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(width * dpr) || c.height !== Math.round(height * dpr)) { c.width = Math.round(width * dpr); c.height = Math.round(height * dpr); }
    const g = c.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, height);
    const css = getComputedStyle(c);
    const played = css.getPropertyValue("--wf-played").trim() || "#3b64e3";
    const rest = css.getPropertyValue("--wf-rest").trim() || "#aab4c6";
    const b = bars.current;
    const px = duration > 0 ? (current / duration) * width : 0;
    const mid = height / 2;
    for (let i = 0; i < b.length; i++) {
      const x = i * (BAR + GAP);
      const h = Math.max(2, b[i] * (height - 6));
      g.fillStyle = x + BAR / 2 <= px ? played : rest;
      g.fillRect(x, mid - h / 2, BAR, h);
    }
    g.fillStyle = played;
    g.fillRect(Math.min(width - 2, Math.max(0, px - 1)), 0, 2, height);          // текущая позиция
  }, [width, height, current, duration]);
  useEffect(() => { draw(); }, [draw, peaks]);

  const seekAt = (clientX: number) => {
    const el = box.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const x = clientX - r.left;
    const t = timeAtX(x, r.width, duration);
    setHover({ x: Math.min(r.width, Math.max(0, x)), t });
    return t;
  };
  const down = (e: React.PointerEvent) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    dragging.current = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const t = seekAt(e.clientX);
    if (t !== undefined) onSeek(t);
  };
  const move = (e: React.PointerEvent) => {
    const t = seekAt(e.clientX);
    if (dragging.current && t !== undefined) onSeek(t);
  };
  const up = (e: React.PointerEvent) => {
    dragging.current = false;
    try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* уже отпущен */ }
    if (e.pointerType !== "mouse") setHover(null);
  };

  const pct = duration > 0 ? Math.min(100, (current / duration) * 100) : 0;
  return (
    <div ref={box} className={`wf ${peaks ? "has-peaks" : "plain"}`} style={{ height }} role="slider" tabIndex={0} aria-label={label}
         aria-valuemin={0} aria-valuemax={Math.round(duration)} aria-valuenow={Math.round(current)} aria-valuetext={`${fmtTime(current)} из ${fmtTime(duration)}`}
         onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onPointerLeave={() => { if (!dragging.current) setHover(null); }}>
      {peaks ? <canvas ref={canvas} style={{ width: "100%", height }} aria-hidden /> : <div className="wf-plain" aria-hidden><i style={{ width: `${pct}%` }} /></div>}
      {hover && duration > 0 && <div className="wf-tip" style={{ left: hover.x }}>{fmtTime(hover.t)}</div>}
    </div>
  );
}
