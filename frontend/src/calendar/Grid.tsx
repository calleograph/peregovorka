import { useEffect, useMemo, useRef, useState } from "react";
import {
  dayKey, dayLabel, daysOf, gridWindow, groupByDay, layoutDay, sameDay, selectionOf, timeAt, timeLabel, WEEKDAYS, type CalEvent, type HourWindow, type WorkHours,
} from "../calendarView";
import { Chip } from "./common";

export const HOUR_PX = 52;
const pad = (n: number) => String(n).padStart(2, "0");

interface Sel { day: Date; a: Date; b: Date }

/** Неделя и день: часы по вертикали, дни по горизонтали, события по реальному времени, пересекающиеся — рядом. Интервал выделяется мышью (или двойным щелчком — полчаса). */
export function Grid({ events, from, to, today, work, onOpen, onDay, onSelect }: {
  events: CalEvent[]; from: Date; to: Date; today: Date; work: WorkHours; onOpen: (e: CalEvent) => void; onDay?: (d: Date) => void; onSelect?: (start: Date, minutes: number) => void;
}) {
  const days = daysOf(from, to);
  const by = useMemo(() => groupByDay(events, from, to), [events, from.getTime(), to.getTime()]);        // eslint-disable-line react-hooks/exhaustive-deps
  const win: HourWindow = useMemo(() => gridWindow(by, work), [by, work.from, work.to, work.onlyWork]);   // eslint-disable-line react-hooks/exhaustive-deps
  const hours = win.to - win.from;
  const scroller = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(() => new Date());
  const [sel, setSel] = useState<Sel | null>(null);
  const drag = useRef<Sel | null>(null);

  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 60_000); return () => window.clearInterval(t); }, []);
  // начало прокрутки: рабочие часы (или раньше, если в периоде есть более ранние события)
  const firstHour = useMemo(() => {
    let earliest = 24, inWork = false;
    for (const list of by.values()) for (const s of list) {
      if (s.ev.all_day) continue;
      earliest = Math.min(earliest, s.start.getHours());
      if (s.end.getHours() >= work.from && s.start.getHours() < work.to) inWork = true;
    }
    return inWork || earliest >= 24 ? work.from : Math.min(work.from, earliest);          // события вне рабочих часов не прячут начало рабочего дня; нет ни одного в рабочих — показываем самое раннее
  }, [by, work.from, work.to]);
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = Math.max(0, (firstHour - win.from) * HOUR_PX - 6); }, [from.getTime(), win.from, firstHour]);   // eslint-disable-line react-hooks/exhaustive-deps

  const allDay = days.map((d) => (by.get(dayKey(d)) ?? []).filter((s) => s.ev.all_day));
  const nowFrac = (now.getHours() * 60 + now.getMinutes() - win.from * 60) / (hours * 60);
  const frac = (e: React.PointerEvent<HTMLDivElement> | React.MouseEvent<HTMLDivElement>) => { const r = e.currentTarget.getBoundingClientRect(); return (e.clientY - r.top) / r.height; };
  const onDown = (d: Date) => (e: React.PointerEvent<HTMLDivElement>) => {
    if (!onSelect || e.button !== 0 || e.pointerType === "touch" || (e.target as HTMLElement).closest(".cal-slot")) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const t = timeAt(d, frac(e), win);
    drag.current = { day: d, a: t, b: t };
    setSel(drag.current);
  };
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = { ...drag.current, b: timeAt(drag.current.day, frac(e), win) };
    setSel(drag.current);
  };
  const onUp = () => {
    const s = drag.current;
    drag.current = null; setSel(null);
    if (!s || !onSelect || s.a.getTime() === s.b.getTime()) return;                // без протяжки — не создаём: это просто щелчок
    const r = selectionOf(s.a, s.b);
    onSelect(r.start, r.minutes);
  };
  const onDouble = (d: Date) => (e: React.MouseEvent<HTMLDivElement>) => {
    if (!onSelect || (e.target as HTMLElement).closest(".cal-slot")) return;
    const r = selectionOf(timeAt(d, frac(e), win), timeAt(d, frac(e), win), true);
    onSelect(r.start, r.minutes);
  };
  const selBox = (s: Sel) => {
    const r = selectionOf(s.a, s.b);
    const a = (r.start.getHours() * 60 + r.start.getMinutes() - win.from * 60) / (hours * 60);
    return { top: `${Math.max(0, a) * 100}%`, height: `${Math.min(1 - Math.max(0, a), r.minutes / (hours * 60)) * 100}%`, label: `${timeLabel(r.start.toISOString())}–${timeLabel(new Date(r.start.getTime() + r.minutes * 60000).toISOString())}` };
  };
  const offTop = Math.max(0, (work.from - win.from) / hours), offBottom = Math.max(0, (win.to - work.to) / hours);

  return (
    <div className="cal-grid" style={{ ["--cols" as string]: days.length }}>
      <div className="cal-gh">
        <span />
        {days.map((d) => <button key={dayKey(d)} type="button" className={`cal-dayhead${sameDay(d, today) ? " today" : ""}`} onClick={() => onDay?.(d)} disabled={!onDay}
                                 aria-label={dayLabel(d)}>{WEEKDAYS[(d.getDay() + 6) % 7]} <b>{d.getDate()}</b></button>)}
      </div>
      {allDay.some((x) => x.length) && (
        <div className="cal-gh allday"><span className="cal-hour">весь день</span>{allDay.map((list, i) => <div key={i} className="cal-allcol">{list.map((s) => <Chip key={s.ev.id} ev={s.ev} onOpen={onOpen} compact />)}</div>)}</div>
      )}
      <div className="cal-scroll" ref={scroller}>
        <div className="cal-body" style={{ height: hours * HOUR_PX }}>
          <div className="cal-hours" aria-hidden>{Array.from({ length: hours }, (_, i) => <span key={i} style={{ top: i * HOUR_PX }}>{pad(win.from + i)}:00</span>)}</div>
          {days.map((d) => {
            const placed = layoutDay(by.get(dayKey(d)) ?? [], d, win);
            const isToday = sameDay(d, today);
            return (
              <div key={dayKey(d)} className={`cal-col${isToday ? " today" : ""}${onSelect ? " pick" : ""}`} role="group" aria-label={dayLabel(d)}
                   onPointerDown={onDown(d)} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={() => { drag.current = null; setSel(null); }} onDoubleClick={onDouble(d)}>
                {offTop > 0 && <i className="cal-off" style={{ top: 0, height: `${offTop * 100}%` }} aria-hidden />}
                {offBottom > 0 && <i className="cal-off" style={{ bottom: 0, height: `${offBottom * 100}%` }} aria-hidden />}
                {Array.from({ length: hours }, (_, i) => <i key={i} className="cal-line" style={{ top: i * HOUR_PX }} />)}
                {isToday && nowFrac >= 0 && nowFrac <= 1 && <i className="cal-now" style={{ top: `${nowFrac * 100}%` }} aria-hidden title={`Сейчас ${pad(now.getHours())}:${pad(now.getMinutes())}`} />}
                {placed.map((p) => (
                  <div key={`${p.slice.ev.id}-${p.col}`} className="cal-slot" style={{ top: `${p.top * 100}%`, height: `${p.height * 100}%`, left: `${(p.col / p.cols) * 100}%`, width: `${100 / p.cols}%` }}>
                    <Chip ev={p.slice.ev} onOpen={onOpen} />
                  </div>
                ))}
                {sel && sameDay(sel.day, d) && (() => { const b = selBox(sel); return <div className="cal-sel" style={{ top: b.top, height: b.height }} aria-hidden><span>{b.label}</span></div>; })()}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
