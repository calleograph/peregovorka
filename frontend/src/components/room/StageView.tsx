import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { layoutStage, type Choice, type Rect, type StageItem } from "../../stage/stageModel";

export interface CellState { big: boolean; hidden: boolean; rect: Rect }

const FLIP_MS = 200;

/**
 * Сцена встречи: все плитки — дети ОДНОГО контейнера с абсолютными координатами и устойчивыми ключами. Смена режима, закрепление, новый показ
 * экрана меняют только координаты: элементы <video> и окно доски не пересоздаются, дорожки не переподписываются. Переход анимируется
 * приёмом FLIP (transform, 200 мс) и отключается при «уменьшении движения» в системе. Лента прокручивается колесом и кнопками без
 * прокручиваемого контейнера; ушедшие за край плитки скрываются (visibility) — адаптивный поток LiveKit перестаёт их грузить.
 */
export default function StageView({ items, choice, rest, mobile, dense = false, render, badges, board, overlay, onSwipe, onEscape, label }: {
  items: StageItem[]; choice: Choice; rest: string[]; mobile: boolean;
  /** Узкая лента участников (режим «Развернуть доску»): главному элементу остаётся почти вся высота. */
  dense?: boolean;
  render: (it: StageItem, s: CellState) => ReactNode;
  badges?: (it: StageItem) => ReactNode;
  /** Окно доски: стоит на месте крупной плитки «доска», иначе спрятано (но не размонтировано — прогретый редактор не грузится заново). */
  board?: (rect: Rect | null) => ReactNode;
  overlay?: ReactNode;
  onSwipe?: (dir: 1 | -1) => void;
  onEscape?: () => void;
  label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [offset, setOffset] = useState(0);
  const cells = useRef(new Map<string, HTMLDivElement>());
  const prev = useRef<{ rects: Record<string, Rect>; w: number; h: number; offset: number }>({ rects: {}, w: 0, h: 0, offset: 0 });

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setSize((s) => (s.w === el.clientWidth && s.h === el.clientHeight ? s : { w: el.clientWidth, h: el.clientHeight }));
    measure();
    let raf = 0;
    const ro = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(measure); });       // не в самом колбэке: иначе «ResizeObserver loop completed with undelivered notifications»
    ro.observe(el);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  const geo = useMemo(() => layoutStage({ main: choice.main, rest, mode: choice.mode, w: size.w, h: size.h, offset, mobile, dense }), [choice, rest, size, offset, mobile, dense]);
  const max = geo.strip?.max ?? 0;
  useEffect(() => { if (offset > max) setOffset(max); }, [offset, max]);
  const hidden = useMemo(() => new Set(geo.hidden), [geo]);
  const big = useMemo(() => new Set(choice.mode === "grid" && !geo.strip ? [] : choice.main), [choice, geo]);

  // FLIP: элемент уже стоит на новом месте; анимируем «из старого» к нему. Изменение размера окна и прокрутка ленты — без анимации.
  useLayoutEffect(() => {
    const p = prev.current;
    const calm = p.w === size.w && p.h === size.h && p.offset === offset;
    const reduce = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (calm && !reduce) {
      for (const [k, r] of Object.entries(geo.rects)) {
        const was = p.rects[k], el = cells.current.get(k);
        if (!was || !el || !r.w || !r.h || (was.x === r.x && was.y === r.y && was.w === r.w && was.h === r.h)) continue;
        el.animate?.([{ transform: `translate(${was.x - r.x}px, ${was.y - r.y}px) scale(${was.w / r.w}, ${was.h / r.h})` }, { transform: "none" }],
          { duration: FLIP_MS, easing: "cubic-bezier(.2,.7,.2,1)" });
      }
    }
    prev.current = { rects: geo.rects, w: size.w, h: size.h, offset };
  }, [geo, size, offset]);

  // колесо над лентой — прокрутка ленты, а не страницы
  const stripRef = useRef(geo.strip);
  stripRef.current = geo.strip;
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const s = stripRef.current;
      if (!s || !s.max || e.defaultPrevented) return;
      const r = el.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      if (x < s.x || x > s.x + s.w || y < s.y || y > s.y + s.h) return;
      e.preventDefault();
      const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      setOffset((o) => Math.min(s.max, Math.max(0, o + d * (e.deltaMode === 1 ? 32 : 1))));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // телефон: свайп по главному элементу — соседний элемент сцены
  const down = useRef<{ x: number; y: number; t: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => { if (mobile && e.pointerType !== "mouse") down.current = { x: e.clientX, y: e.clientY, t: Date.now() }; };
  const onPointerUp = (e: React.PointerEvent) => {
    const d = down.current;
    down.current = null;
    if (!d || !onSwipe) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (Math.abs(dx) > 60 && Math.abs(dy) < 50 && Date.now() - d.t < 700) onSwipe(dx < 0 ? 1 : -1);
  };

  const step = geo.strip ? (geo.strip.vertical ? geo.strip.h : geo.strip.w) * 0.8 : 0;
  const boardRect = big.has("board") ? geo.rects.board ?? null : null;
  return (
    <div className={`st-view mode-${choice.mode} ${mobile ? "mobile" : ""}`} ref={box} role="region" aria-label={label}
         data-main={choice.main.join(" ")} data-reason={choice.reason}
         onPointerDown={onPointerDown} onPointerUp={onPointerUp}
         onKeyDown={(e) => { if (e.key === "Escape" && onEscape && !document.fullscreenElement) onEscape(); }}>
      {items.map((it) => {
        const r = geo.rects[it.key];
        const isHidden = !r || hidden.has(it.key);
        const isBig = big.has(it.key);
        return (
          <div key={it.key} ref={(el) => { if (el) cells.current.set(it.key, el); else cells.current.delete(it.key); }}
               className={`st-cell type-${it.type} ${isBig ? "big" : "small"}`} data-key={it.key} aria-hidden={isHidden || undefined}
               style={r ? { left: r.x, top: r.y, width: r.w, height: r.h, visibility: isHidden ? "hidden" : undefined } : { visibility: "hidden", left: 0, top: 0, width: 1, height: 1 }}>
            {render(it, { big: isBig, hidden: isHidden, rect: r ?? { x: 0, y: 0, w: 0, h: 0 } })}
            {badges && <div className="st-badges">{badges(it)}</div>}
          </div>
        );
      })}
      {board && <div className={`st-board ${boardRect ? "on" : ""}`} style={boardRect ? { left: boardRect.x, top: boardRect.y, width: boardRect.w, height: boardRect.h } : undefined}>{board(boardRect)}</div>}
      {geo.strip && max > 0 && (
        <>
          <button type="button" className={`st-scroll prev ${geo.strip.vertical ? "v" : "h"}`} disabled={offset <= 0} aria-label="Лента: назад"
                  style={geo.strip.vertical ? { left: geo.strip.x + geo.strip.w / 2 - 16, top: 4 } : { left: 4, top: geo.strip.y + geo.strip.h / 2 - 16 }}
                  onClick={() => setOffset((o) => Math.max(0, o - step))}>‹</button>
          <button type="button" className={`st-scroll next ${geo.strip.vertical ? "v" : "h"}`} disabled={offset >= max} aria-label="Лента: дальше"
                  style={geo.strip.vertical ? { left: geo.strip.x + geo.strip.w / 2 - 16, top: geo.strip.h - 36 } : { left: geo.strip.w - 36, top: geo.strip.y + geo.strip.h / 2 - 16 }}
                  onClick={() => setOffset((o) => Math.min(max, o + step))}>›</button>
        </>
      )}
      {overlay}
    </div>
  );
}
