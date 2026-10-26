import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { calendarApi, type CalendarEvents, type CalendarInfo, type CalendarStatus } from "../calendarApi";
import { healthOf } from "../calendarHealth";
import { loadSession, saveCalendars, saveDay, saveMode, saveWork } from "../calendarPrefs";
import {
  cleanWork, dateInput, dayKey, dayLabel, daysOf, groupByDay, isConference, localZone, MODES, nextSlot, rangeFor, sameDay, shift, timeLabel, title as periodTitle, TONES, WEEKDAYS,
  type CalEvent, type Slice, type ViewMode, type WorkHours,
} from "../calendarView";
import { CreateDialog } from "../calendar/CreateDialog";
import { Chip, errText, Pop } from "../calendar/common";
import { EventDialog } from "../calendar/EventDialog";
import { Grid } from "../calendar/Grid";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";

const REFRESH_MS = 5 * 60_000;          // фоновое обновление открытой страницы: не чаще раза в пять минут (сервер к тому же кэширует ответ Exchange)

/** Узкое окно (телефон, узкая панель): ширина до 640 px. */
function useNarrow(max = 640): boolean {
  const q = `(max-width: ${max}px)`;
  const [n, setN] = useState(() => typeof window !== "undefined" && window.matchMedia?.(q).matches === true);
  useEffect(() => {
    const m = window.matchMedia?.(q);
    if (!m) return;
    const f = () => setN(m.matches);
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, [q]);
  return n;
}

// ------------------------------------------------------------------------------------------------ виды
function MonthView({ events, anchor, today, onOpen, onDay }: { events: CalEvent[]; anchor: Date; today: Date; onOpen: (e: CalEvent) => void; onDay: (d: Date) => void }) {
  const { from, to } = rangeFor("month", anchor);
  const days = daysOf(from, to);
  const [expanded, setExpanded] = useState<string | null>(null);          // ячейка, раскрытая до полного списка
  const by = useMemo(() => groupByDay(events, from, to), [events, from.getTime(), to.getTime()]);        // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="cal-month" role="grid" aria-label="Месяц">
      {WEEKDAYS.map((w) => <div key={w} className="cal-dow" role="columnheader">{w}</div>)}
      {days.map((d) => {
        const list = by.get(dayKey(d)) ?? [];
        const open = expanded === dayKey(d);
        return (
          <div key={dayKey(d)} role="gridcell" className={`cal-cell${d.getMonth() !== anchor.getMonth() ? " other" : ""}${sameDay(d, today) ? " today" : ""}${open ? " open" : ""}`}>
            <button type="button" className="cal-daynum" onClick={() => onDay(d)} aria-label={`Открыть день: ${dayLabel(d)}`}>{d.getDate()}</button>
            {(open ? list : list.slice(0, 3)).map((s) => <Chip key={`${s.ev.id}-${dayKey(d)}`} ev={s.ev} onOpen={onOpen} compact />)}
            {list.length > 3 && <button type="button" className="cal-more" aria-expanded={open} onClick={() => setExpanded(open ? null : dayKey(d))}>{open ? "свернуть" : `ещё ${list.length - 3}`}</button>}
          </div>
        );
      })}
    </div>
  );
}

function ListView({ events, from, to, onOpen }: { events: CalEvent[]; from: Date; to: Date; onOpen: (e: CalEvent) => void }) {
  const by = useMemo(() => groupByDay(events, from, to), [events, from.getTime(), to.getTime()]);        // eslint-disable-line react-hooks/exhaustive-deps
  const days = daysOf(from, to).filter((d) => (by.get(dayKey(d)) ?? []).length);
  if (!days.length) return <p className="muted cal-empty">В этот период событий нет.</p>;
  return (
    <div className="cal-list">
      {days.map((d) => (
        <section key={dayKey(d)} aria-label={dayLabel(d)}>
          <h3>{dayLabel(d)}</h3>
          {(by.get(dayKey(d)) as Slice[]).map((s) => (
            <div key={`${s.ev.id}-${dayKey(d)}`} className="cal-row">
              <span className="cal-rowtime">{s.ev.all_day ? "весь день" : `${timeLabel(s.ev.start)}–${timeLabel(s.ev.end)}`}</span>
              <Chip ev={s.ev} onOpen={onOpen} />
              <span className="muted small">{TONES[s.ev.status === "cancelled" ? "cancelled" : s.ev.recurring ? "series" : isConference(s.ev) || s.ev.meeting ? "meeting" : "plain"].label}{isConference(s.ev) ? " · Peregovorka" : ""}</span>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ подключение
function Connect({ status, onDone }: { status: CalendarStatus; onDone: (s: CalendarStatus) => void }) {
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const go = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { onDone(await calendarApi.connect()); } catch (x) { setErr(errText(x)); setBusy(false); }
  };
  return (
    <form className="card form cal-connect" onSubmit={(e) => void go(e)}>
      <h2>Подключить календарь Outlook</h2>
      <p>Peregovorka покажет ваши встречи из Outlook и поможет назначать новые: они появятся в вашем календаре, а приглашения участникам разошлёт Exchange.</p>
      <ul className="cal-facts">
        <li><Icon name="check" size={15} /><span>Доступ только к календарю ящика <b>{status.email}</b> — вашему собственному. Чужие календари не открываются.</span></li>
        <li><Icon name="check" size={15} /><span>Ваш пароль не запрашивается и не сохраняется: доступ к Exchange настроил администратор организации.</span></li>
        <li><Icon name="check" size={15} /><span>Сервер читает название, время и место событий; тексты писем и вложения не читаются.</span></li>
        <li><Icon name="check" size={15} /><span>{status.can_write ? "Новые встречи создаются в вашем календаре от вашего имени." : "Создание встреч из Peregovorka администратор не включил: календарь только для просмотра."}</span></li>
        <li><Icon name="check" size={15} /><span>Отключить календарь можно в любой момент кнопкой «Отключить».</span></li>
      </ul>
      <label className="check"><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} /> Разрешаю Peregovorka показывать и вести мой календарь Outlook</label>
      {err && <div className="alert error" role="alert">{err}<br /><span className="small">Если ошибка повторяется, передайте её администратору: у сервера нет доступа к вашему ящику или адрес почты в каталоге неверен.</span></div>}
      <div className="row"><button className="btn primary" disabled={!agree || busy}>{busy ? "Проверяем доступ…" : "Подключить календарь"}</button></div>
    </form>
  );
}

// ------------------------------------------------------------------------------------------------ состояние подключения
// ------------------------------------------------------------------------------------------------ страница
export default function CalendarPage() {
  const init = useMemo(loadSession, []);                         // вид, дата, календари и рабочие часы помнятся между переходами по разделам
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const narrow = useNarrow();
  const [wanted, setMode] = useState<ViewMode>(init.mode);
  const mode: ViewMode = narrow && wanted === "week" ? "day" : wanted;          // семь колонок на узком экране нечитаемы: там неделя заменена днём
  const [anchor, setAnchor] = useState(init.anchor);
  const [work, setWork] = useState<WorkHours>(init.work);
  const [cals, setCals] = useState<string[]>(init.calendars);
  const [calList, setCalList] = useState<CalendarInfo[]>([]);
  const [data, setData] = useState<CalendarEvents | null>(null);
  const [err, setErr] = useState("");
  const [code, setCode] = useState("");
  const [lastOk, setLastOk] = useState<Date | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<CalEvent | null>(null);
  const [creating, setCreating] = useState<{ at: Date; minutes?: number } | null>(null);
  const seq = useRef(0);
  const [toast, show] = useToast();
  const today = new Date();
  const range = useMemo(() => rangeFor(mode, anchor), [mode, anchor.getFullYear(), anchor.getMonth(), anchor.getDate()]);        // eslint-disable-line react-hooks/exhaustive-deps

  const loadStatus = useCallback(() => calendarApi.status().then(setStatus).catch((e) => setErr(errText(e))), []);
  useEffect(() => { void loadStatus(); }, [loadStatus]);
  useEffect(() => {
    if (!status?.connected) return;
    calendarApi.calendars().then((r) => { setCalList(r.calendars); setCals((cur) => { const ok = cur.filter((c) => r.calendars.some((x) => x.id === c)); return ok.length ? ok : ["primary"]; }); }).catch(() => undefined);
  }, [status?.connected]);

  const load = useCallback(async (refresh = false) => {
    if (!status?.connected) return;
    const my = ++seq.current;                                                  // устаревший ответ (быстро переключили период) отбрасывается
    setLoading(true);
    try {
      const r = await calendarApi.events(range.from, range.to, refresh, cals);
      if (my === seq.current) { setData(r); setErr(""); setCode(""); if (!r.stale && !r.error) setLastOk(new Date()); else if (r.last_ok_at) setLastOk(new Date(r.last_ok_at)); }
    } catch (e) {
      if (my === seq.current) { setErr(errText(e)); setCode((e as { code?: string }).code ?? ""); if ((e as { code?: string }).code === "not_connected") void loadStatus(); }
    } finally { if (my === seq.current) setLoading(false); }
  }, [status?.connected, range.from, range.to, cals, loadStatus]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const t = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, REFRESH_MS);
    const vis = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [load]);

  const go = (d: Date) => { setAnchor(d); saveDay(d); };
  const pickMode = (m: ViewMode) => { setMode(m); saveMode(m); };
  const toDay = (d: Date) => { go(d); pickMode("day"); };
  const pickWork = (w: Partial<WorkHours>) => { const n = cleanWork({ ...work, ...w }); setWork(n); saveWork(n); };
  const toggleCal = (id: string) => setCals((cur) => { const n = cur.includes(id) ? cur.filter((c) => c !== id) : [...cur, id]; const out = n.length ? n : cur; saveCalendars(out); return out; });
  const health = healthOf({ loading, err, code, data, lastOk });

  if (!status) return <section className="cal-page"><h1>Календарь</h1>{err ? <div className="alert error" role="alert">{err}</div> : <p className="muted">Загрузка…</p>}</section>;
  if (!status.available) {
    return (
      <section className="cal-page"><h1>Календарь</h1>
        <div className="alert info" role="status">{status.message}{status.reason === "disabled" || status.reason === "no_connection" ? <> Администратор включает раздел в <Link to="/admin">Администрирование → Microsoft Outlook → «Календарь в Peregovorka»</Link>.</> : null}</div>
      </section>
    );
  }
  if (!status.connected) return <section className="cal-page"><h1>Календарь</h1><Connect status={status} onDone={(s) => { setStatus(s); show("Календарь подключён", "ok"); }} /></section>;

  const events = data?.events ?? [];
  const hoursOptions = Array.from({ length: 25 }, (_, h) => h);
  return (
    <section className="cal-page">
      <header className="cal-head">
        <h1>Календарь</h1>
        {status.can_write && <button className="btn primary" onClick={() => setCreating({ at: nextSlot(new Date()) })}><Icon name="plus" size={15} /> Создать встречу</button>}
        <div className="cal-nav" role="group" aria-label="Период">
          <button className="btn" onClick={() => go(new Date())}>Сегодня</button>
          <button className="btn icon" aria-label="Назад" title="Назад" onClick={() => go(shift(mode, anchor, -1))}><Icon name="chevronL" size={16} /></button>
          <button className="btn icon" aria-label="Вперёд" title="Вперёд" onClick={() => go(shift(mode, anchor, 1))}><Icon name="chevronR" size={16} /></button>
          <label className="cal-jump"><span className="sr-only">Перейти к дате</span><input type="date" value={dateInput(anchor)} onChange={(e) => { if (e.target.value) go(new Date(`${e.target.value}T12:00`)); }} aria-label="Перейти к дате" /></label>
        </div>
        <h2 className="cal-period" aria-live="polite">{periodTitle(mode, anchor)}</h2>
        <div className="spacer" />
        <div className="seg" role="group" aria-label="Вид">{MODES.filter((m) => !(narrow && m.id === "week")).map((m) => <button key={m.id} type="button" aria-pressed={mode === m.id} onClick={() => pickMode(m.id)}>{m.label}</button>)}</div>
        {calList.length > 1 && (
          <Pop label="Календари" icon="calendar" align="right">
            <fieldset className="cal-cals"><legend>Показывать календари</legend>
              {calList.map((c) => <label key={c.id} className="check"><input type="checkbox" checked={cals.includes(c.id)} onChange={() => toggleCal(c.id)} /> {c.name}{c.default ? " (основной)" : ""}</label>)}
              <p className="muted small">Только календари вашего ящика. Новые встречи создаются в основном календаре.</p>
            </fieldset>
          </Pop>
        )}
        {(mode === "week" || mode === "day") && (
          <Pop label="Рабочие часы" icon="gear" align="right">
            <fieldset className="cal-cals"><legend>Рабочие часы</legend>
              <div className="row wrap">
                <label>с <select value={work.from} onChange={(e) => pickWork({ from: Number(e.target.value) })}>{hoursOptions.slice(0, 23).map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select></label>
                <label>до <select value={work.to} onChange={(e) => pickWork({ to: Number(e.target.value) })}>{hoursOptions.slice(1).map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select></label>
              </div>
              <label className="check"><input type="checkbox" checked={work.onlyWork} onChange={(e) => pickWork({ onlyWork: e.target.checked })} /> Показывать только рабочие часы</label>
              <p className="muted small">События вне рабочих часов не скрываются: сетка расширяется до них.</p>
            </fieldset>
          </Pop>
        )}
        <button className="btn" onClick={() => void load(true)} disabled={loading} title="Обновить из Exchange"><Icon name="retry" size={15} /> {loading ? "Обновляем…" : "Обновить"}</button>
      </header>
      <div className={`cal-health h-${health.kind}`} role="status" aria-live="polite">
        <span className="dot" aria-hidden /> <span>{health.text}</span>
        {(health.kind === "down" || health.kind === "stale") && <button className="linkish" onClick={() => void load(true)}>Повторить</button>}
        {health.kind === "relogin" && <button className="linkish" onClick={() => { void loadStatus(); }}>Подключить заново</button>}
      </div>
      <ul className="cal-legend" aria-label="Обозначения">
        <li className="t-meeting">Разовая встреча</li><li className="t-series"><Icon name="retry" size={12} /> Повторяющаяся</li><li className="t-plain">Событие Outlook</li><li className="t-cancelled"><s>Отменено</s></li>
        <li className="lg-conf"><span className="cal-conf" aria-hidden><Icon name="video" size={11} /></span> Конференция Peregovorka</li>
      </ul>
      <div className="cal-view" aria-busy={loading}>
        {mode === "month" && <MonthView events={events} anchor={anchor} today={today} onOpen={setOpen} onDay={toDay} />}
        {mode === "week" && <Grid events={events} from={range.from} to={range.to} today={today} work={work} onOpen={setOpen} onDay={toDay} onSelect={status.can_write ? (at, minutes) => setCreating({ at, minutes }) : undefined} />}
        {mode === "day" && <Grid events={events} from={range.from} to={range.to} today={today} work={work} onOpen={setOpen} onSelect={status.can_write ? (at, minutes) => setCreating({ at, minutes }) : undefined} />}
        {mode === "list" && <ListView events={events} from={range.from} to={range.to} onOpen={setOpen} />}
      </div>
      <footer className="cal-foot muted small">
        Календарь ящика {status.email}. Время показано в вашем часовом поясе ({localZone()}). {status.can_write ? ((mode === "week" || mode === "day") ? "Чтобы создать встречу, выделите интервал мышью или дважды щёлкните по времени. " : "") : "Только просмотр. "}
        <button className="linkish" onClick={async () => { try { setStatus(await calendarApi.disconnect()); setData(null); } catch (e) { setErr(errText(e)); } }}>Отключить календарь</button>
      </footer>
      {open && <EventDialog ev={open} status={status} onClose={() => setOpen(null)} onChanged={() => void load(true)} />}
      {creating && <CreateDialog status={status} initial={creating.at} minutes={creating.minutes} onClose={() => setCreating(null)} onCreated={() => void load(true)} />}
      {toast}
    </section>
  );
}
