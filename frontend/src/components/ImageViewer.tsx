import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent as RPointerEvent, type WheelEvent as RWheelEvent } from "react";
import { createPortal } from "react-dom";
import { clampPan, clampScale, dist, doubleTapTarget, fitScale, MAX_ZOOM, step, wheelFactor, zoomAt, type View } from "../imageViewMath";
import { Icon } from "./Icons";

export interface ViewerImage { id: string; name: string; size: number }

/**
 * Просмотрщик изображений чата поверх комнаты (без новой вкладки): колесо — масштаб к курсору, перетаскивание — сдвиг, щипок на сенсорном экране, двойной щелчок,
 * ← → — соседние изображения чата, Esc / × / щелчок вне окна — закрыть. Окно можно двигать за верхнюю панель и менять его размер (угол); комната под ним остаётся на месте.
 * Свой лёгкий компонент без сторонних библиотек; подгружается лениво — при первом открытии изображения.
 */
export default function ImageViewer({ images, index, load, onClose, onIndex }: { images: ViewerImage[]; index: number; load: (id: string) => Promise<Blob>; onClose: () => void; onIndex: (i: number) => void }) {
  const img = images[index];
  const cache = useRef(new Map<string, string>());
  const [url, setUrl] = useState("");
  const [err, setErr] = useState("");
  const stage = useRef<HTMLDivElement>(null);
  const win = useRef<HTMLDivElement>(null);
  const [nat, setNat] = useState({ w: 0, h: 0 });
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View>({ s: 1, x: 0, y: 0 });
  const [fitMode, setFitMode] = useState(true);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ view: View; d0: number } | null>(null);
  const drag = useRef<{ x: number; y: number; view: View } | null>(null);
  const fit = fitScale(box.w, box.h, nat.w, nat.h);

  const get = useCallback(async (it: { id: string }) => {
    const hit = cache.current.get(it.id);
    if (hit) return hit;
    const u = URL.createObjectURL(await load(it.id));
    cache.current.set(it.id, u);
    return u;
  }, [load]);

  useEffect(() => () => { cache.current.forEach((u) => URL.revokeObjectURL(u)); cache.current.clear(); }, []);
  useEffect(() => {
    let alive = true;
    setErr(""); setUrl(""); setNat({ w: 0, h: 0 }); setFitMode(true);
    get(img).then((u) => { if (alive) setUrl(u); }).catch(() => { if (alive) setErr("Не удалось загрузить изображение"); });
    for (const d of [-1, 1]) { const n = images[index + d]; if (n) void get(n).catch(() => undefined); }      // соседние — заранее
    return () => { alive = false; };
  }, [img, index, images, get]);

  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setBox({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);
  useEffect(() => { if (fitMode) setView({ s: fit, x: 0, y: 0 }); }, [fit, fitMode, nat.w]);

  const apply = (v: View) => { setFitMode(false); setView(clampPan(v, box.w, box.h, nat.w, nat.h)); };
  const zoomBy = (f: number, cx = 0, cy = 0) => apply(zoomAt(view, view.s * f, cx, cy, fit));
  const go = (d: number) => { const i = step(index, d, images.length); if (i !== index) onIndex(i); };
  const rel = (e: { clientX: number; clientY: number }) => { const r = stage.current!.getBoundingClientRect(); return { x: e.clientX - r.left - r.width / 2, y: e.clientY - r.top - r.height / 2 }; };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "+" || e.key === "=") zoomBy(1.25);
      else if (e.key === "-" || e.key === "_") zoomBy(0.8);
      else if (e.key === "0") apply({ s: 1, x: 0, y: 0 });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEffect(() => { win.current?.focus(); }, []);

  const onWheel = (e: RWheelEvent) => { const p = rel(e); zoomBy(wheelFactor(e.deltaY, e.deltaMode), p.x, p.y); };
  const onDown = (e: RPointerEvent) => {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, rel(e));
    if (pointers.current.size === 2) { const [a, b] = [...pointers.current.values()]; gesture.current = { view, d0: dist(a, b) }; drag.current = null; }
    else drag.current = { ...rel(e), view };
  };
  const onMove = (e: RPointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, rel(e));
    if (pointers.current.size === 2 && gesture.current) {
      const [a, b] = [...pointers.current.values()];
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      apply(zoomAt(gesture.current.view, gesture.current.view.s * (dist(a, b) / (gesture.current.d0 || 1)), mid.x, mid.y, fit));
    } else if (drag.current) {
      const p = rel(e);
      apply({ ...drag.current.view, x: drag.current.view.x + p.x - drag.current.x, y: drag.current.view.y + p.y - drag.current.y });
    }
  };
  const onUp = (e: RPointerEvent) => { pointers.current.delete(e.pointerId); gesture.current = null; drag.current = pointers.current.size === 1 ? { ...[...pointers.current.values()][0], view } : null; };
  const onDouble = (e: RPointerEvent | React.MouseEvent) => { const p = rel(e); const t = doubleTapTarget(view.s, fit); t === fit ? (setFitMode(true)) : apply(zoomAt(view, t, p.x, p.y, fit)); };

  // перемещение окна за верхнюю панель
  const onHeadDown = (e: RPointerEvent) => {
    if ((e.target as HTMLElement).closest("button")) return;
    const w = win.current!;
    const r = w.getBoundingClientRect();
    const dx = e.clientX - r.left, dy = e.clientY - r.top;
    const move = (ev: PointerEvent) => setPos({ left: Math.min(window.innerWidth - 120, Math.max(-r.width + 120, ev.clientX - dx)), top: Math.min(window.innerHeight - 48, Math.max(0, ev.clientY - dy)) });
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const download = () => { const a = document.createElement("a"); a.href = url; a.download = img.name; document.body.appendChild(a); a.click(); a.remove(); };
  const pct = Math.round(view.s * 100);
  return createPortal(
    <div className="iv-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="iv-window" ref={win} role="dialog" aria-modal="true" aria-label={`Просмотр изображения: ${img.name}`} tabIndex={-1}
           style={pos ? { left: pos.left, top: pos.top, transform: "none" } : undefined}>
        <div className="iv-head" onPointerDown={onHeadDown}>
          <span className="iv-title" title={img.name}>{img.name}<span className="muted small"> · {index + 1} из {images.length}</span></span>
          <span className="iv-tools">
            <button type="button" className="icon-btn" onClick={() => go(-1)} disabled={index === 0} aria-label="Предыдущее изображение" title="Предыдущее (←)"><Icon name="chevronL" size={16} /></button>
            <button type="button" className="icon-btn" onClick={() => go(1)} disabled={index === images.length - 1} aria-label="Следующее изображение" title="Следующее (→)"><Icon name="chevronR" size={16} /></button>
            <button type="button" className="icon-btn" onClick={() => zoomBy(0.8)} aria-label="Уменьшить" title="Уменьшить (−)">−</button>
            <span className="iv-pct" aria-live="polite">{pct}%</span>
            <button type="button" className="icon-btn" onClick={() => zoomBy(1.25)} disabled={view.s >= MAX_ZOOM} aria-label="Увеличить" title="Увеличить (+)">+</button>
            <button type="button" className="btn mini ghost" onClick={() => apply({ s: 1, x: 0, y: 0 })} title="Масштаб 100 % (0)">100%</button>
            <button type="button" className="btn mini ghost" onClick={() => setFitMode(true)} title="Вписать в окно">По размеру окна</button>
            <button type="button" className="btn mini ghost" onClick={download} disabled={!url} title="Скачать файл">Скачать</button>
            <button type="button" className="btn mini ghost" onClick={() => window.open(url, "_blank", "noopener")} disabled={!url} title="Открыть оригинал в новой вкладке (только по этой кнопке)">Открыть оригинал</button>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Закрыть" title="Закрыть (Esc)"><Icon name="close" size={16} /></button>
          </span>
        </div>
        <div className="iv-stage" ref={stage} onWheel={onWheel} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onDoubleClick={onDouble}
             style={{ cursor: view.s > fit * 1.01 ? "grab" : "default" }}>
          {err && <div className="iv-msg" role="alert">{err}</div>}
          {!url && !err && <div className="iv-msg muted">Загрузка…</div>}
          {url && <img src={url} alt={img.name} draggable={false} onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
                       style={{ transform: `translate(calc(-50% + ${view.x}px), calc(-50% + ${view.y}px)) scale(${clampScale(view.s, fit)})` }} />}
        </div>
      </div>
    </div>, document.body);
}
