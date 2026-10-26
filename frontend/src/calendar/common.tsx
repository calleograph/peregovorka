import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api";
import { describe, isConference, STATUSES, timeLabel, toneOf, TONES, type CalEvent } from "../calendarView";
import { Icon } from "../components/Icons";

export const errText = (e: unknown): string => (e instanceof ApiError ? e.message : (e as Error)?.message || "Не удалось выполнить действие");

/** Путь внутри этого же сайта или null (ссылки на чужие адреса из событий приложением не открываются как свои). */
export function pathOf(url: string): string | null {
  try { const u = new URL(url, window.location.origin); return u.origin === window.location.origin ? u.pathname : null; } catch { return null; }
}

/** Плашка события: цвет — по главному признаку (разовая, серия, обычное, отменено), конференция Peregovorka — зелёным значком и подписью, серия — значком повторения. */
export function Chip({ ev, onOpen, compact = false }: { ev: CalEvent; onOpen: (e: CalEvent) => void; compact?: boolean }) {
  const tone = toneOf(ev), conf = isConference(ev), st = STATUSES[ev.status];
  return (
    <button type="button" className={`cal-chip ${TONES[tone].cls}${conf ? " conf" : ""}${compact ? " compact" : ""} ${st.cls}${ev.show_as === "free" ? " free" : ""}`} onClick={() => onOpen(ev)}
            aria-label={describe(ev)} title={`${TONES[tone].label}${conf ? " · конференция Peregovorka" : ""} · ${st.label}`}>
      {conf && <span className="cal-conf" aria-hidden><Icon name="video" size={11} /></span>}
      {!ev.all_day && <span className="cal-time">{timeLabel(ev.start)}</span>}
      <span className="cal-title">{ev.title}</span>
      {ev.recurring && <span className="cal-rep" aria-hidden><Icon name="retry" size={11} /></span>}
      {(ev.status === "live" || ev.status === "cancelled") && <span className="cal-state">{st.label}</span>}
    </button>
  );
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
      <button type="button" className="btn" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((v) => !v)}><Icon name={icon} size={15} /> {label}</button>
      {open && <div className={`cal-popbox ${align}`} role="dialog" aria-label={label}>{children}</div>}
    </div>
  );
}
