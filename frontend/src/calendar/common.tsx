import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api";
import { describe, durationText, isConference, STATUSES, timeLabel, toneOf, TONES, type CalEvent } from "../calendarView";
import { Icon } from "../components/Icons";

export const errText = (e: unknown): string => (e instanceof ApiError ? e.message : (e as Error)?.message || "Не удалось выполнить действие");

/** Путь внутри этого же сайта или null (ссылки на чужие адреса из событий приложением не открываются как свои). */
export function pathOf(url: string): string | null {
  try { const u = new URL(url, window.location.origin); return u.origin === window.location.origin ? u.pathname : null; } catch { return null; }
}

export type ChipSize = "s" | "m" | "l";
/** Размер плашки в сетке по её высоте в пикселях: s — одна строка (время и название), m — время и название двумя строками, l — ещё и несколько строк названия. */
export const chipSize = (px: number): ChipSize => (px < 34 ? "s" : px < 60 ? "m" : "l");

/** Плашка события: цвет — по главному признаку (разовая, серия, обычное, отменено), конференция Peregovorka — зелёным значком и подписью, серия — значком повторения.
 *  Подробности (полное название, время, тип, место) — во всплывающей карточке при наведении мыши и при фокусе с клавиатуры; на сенсорных экранах — по нажатию в окне события. */
export function Chip({ ev, onOpen, compact = false, size, lines }: { ev: CalEvent; onOpen: (e: CalEvent) => void; compact?: boolean; size?: ChipSize; lines?: number }) {
  const tone = toneOf(ev), conf = isConference(ev), st = STATUSES[ev.status];
  const btn = useRef<HTMLButtonElement>(null);
  const [tip, setTip] = useState(false);
  const timer = useRef(0);
  const show = (ms: number) => { window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setTip(true), ms); };
  const hide = () => { window.clearTimeout(timer.current); setTip(false); };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (!tip) return;
    const off = () => setTip(false);
    window.addEventListener("scroll", off, true); document.addEventListener("keydown", off);
    return () => { window.removeEventListener("scroll", off, true); document.removeEventListener("keydown", off); };
  }, [tip]);
  const end = ev.all_day ? "" : `–${timeLabel(ev.end)}`;
  return (
    <>
      <button ref={btn} type="button" className={`cal-chip ${TONES[tone].cls}${conf ? " conf" : ""}${compact ? " compact" : ""}${size ? ` sz-${size}` : ""} ${st.cls}${ev.show_as === "free" ? " free" : ""}`}
              style={lines ? { ["--lines" as string]: lines } : undefined} onClick={() => { hide(); onOpen(ev); }}
              onPointerEnter={(e) => { if (e.pointerType === "mouse") show(350); }} onPointerLeave={hide} onFocus={(e) => { if (e.currentTarget.matches(":focus-visible")) show(0); }} onBlur={hide}
              aria-label={describe(ev)}>
        {conf && <span className="cal-conf" aria-hidden><Icon name="video" size={11} /></span>}
        {!ev.all_day && <span className="cal-time">{timeLabel(ev.start)}{size && size !== "s" ? end : ""}</span>}
        <span className="cal-title">{ev.title}</span>
        {ev.recurring && <span className="cal-rep" aria-hidden><Icon name="retry" size={11} /></span>}
        {(ev.status === "live" || ev.status === "cancelled") && <span className="cal-state">{st.label}</span>}
      </button>
      {tip && btn.current && <HoverCard ev={ev} anchor={btn.current} />}
    </>
  );
}

/** Карточка события у плашки: целиком название, дата и время, тип и статус, место, организатор. Не перехватывает мышь и не выходит за окно. */
function HoverCard({ ev, anchor }: { ev: CalEvent; anchor: HTMLElement }) {
  const box = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const a = anchor.getBoundingClientRect(), b = box.current?.getBoundingClientRect();
    if (!b) return;
    const gap = 8, vw = window.innerWidth, vh = window.innerHeight;
    let left = a.right + gap;
    if (left + b.width > vw - 8) left = a.left - gap - b.width;               // справа не помещается — слева
    if (left < 8) left = Math.min(Math.max(8, a.left), vw - b.width - 8);     // узкое окно — под или над плашкой
    let top = Math.min(Math.max(8, a.top), vh - b.height - 8);
    if (left > a.left && left < a.right) top = a.bottom + gap + b.height < vh - 8 ? a.bottom + gap : Math.max(8, a.top - gap - b.height);
    setAt({ left, top });
  }, [anchor]);
  const tone = toneOf(ev), conf = isConference(ev), st = STATUSES[ev.status];
  const s = new Date(ev.start), minutes = Math.round((new Date(ev.end).getTime() - s.getTime()) / 60000);
  return createPortal(
    <div ref={box} className={`cal-hover ${TONES[tone].cls}`} role="tooltip" style={at ? { left: at.left, top: at.top } : { left: 0, top: 0, visibility: "hidden" }}>
      <b className="cal-hover-title">{ev.title}</b>
      <span>{s.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" })}, {ev.all_day ? "весь день" : `${timeLabel(ev.start)}–${timeLabel(ev.end)} · ${durationText(minutes)}`}</span>
      <span className="cal-hover-kind">{TONES[tone].label}{conf ? " · конференция Peregovorka" : ""}{ev.status === "live" || ev.status === "cancelled" ? ` · ${st.label}` : ""}</span>
      {ev.location && <span>Место: {ev.location}</span>}
      {ev.organizer && !ev.mine && <span>Организатор: {ev.organizer}</span>}
    </div>, document.body);
}

/** Небольшое всплывающее окно у кнопки: закрывается Escape, щелчком снаружи и уходом фокуса. */
export function Pop({ label, icon, children, align = "left" }: { label: string; icon: "gear" | "calendar"; children: ReactNode; align?: "left" | "right" }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="cal-pop" ref={box}>
      <button type="button" className="btn cal-tool" aria-expanded={open} aria-haspopup="dialog" title={label} onClick={() => setOpen((v) => !v)}><Icon name={icon} size={16} /><span className="cal-lbl">{label}</span></button>
      {open && <div className={`cal-popbox ${align}`} role="dialog" aria-label={label}>{children}</div>}
    </div>
  );
}
