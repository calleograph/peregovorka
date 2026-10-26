import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api";
import { calendarApi, type CalendarEvents, type CalendarStatus, type CreatedEvent } from "../calendarApi";
import {
  buildCreateBody, dateInput, dayKey, dayLabel, daysOf, defaultForm, describe, DURATIONS, durationText, formError, groupByDay, KINDS, layoutDay, localZone, MODES, newKey, nextSlot, rangeFor,
  REPEATS, sameDay, shift, STATUSES, timeInput, timeLabel, title as periodTitle, WEEKDAYS, type CalEvent, type CreateForm, type Slice, type ViewMode,
} from "../calendarView";
import { PeoplePicker } from "../chat/ChatDialogs";
import type { Person } from "../chat/chatApi";
import { ConfirmDialog, Modal } from "../components/Dialogs";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { copyText } from "../util";
import { useFocusOnMount } from "../useInitialFocus";

const REFRESH_MS = 5 * 60_000;          // фоновое обновление открытой страницы: не чаще раза в пять минут (сервер к тому же кэширует ответ Exchange)
const MODE_KEY = "pg:calendarMode";
const HOUR_PX = 52;

const errText = (e: unknown): string => (e instanceof ApiError ? e.message : (e as Error)?.message || "Не удалось выполнить действие");

function pathOf(url: string): string | null {
  try { const u = new URL(url, window.location.origin); return u.origin === window.location.origin ? u.pathname : null; } catch { return null; }
}

// ------------------------------------------------------------------------------------------------ плашка события
function Chip({ ev, onOpen, compact = false }: { ev: CalEvent; onOpen: (e: CalEvent) => void; compact?: boolean }) {
  const k = KINDS[ev.kind], s = STATUSES[ev.status];
  return (
    <button type="button" className={`cal-chip ${k.cls} ${s.cls}${compact ? " compact" : ""}`} onClick={() => onOpen(ev)} aria-label={describe(ev)} title={`${k.hint} · ${s.label}`}>
      <Icon name={k.icon} size={13} />
      {!ev.all_day && <span className="cal-time">{timeLabel(ev.start)}</span>}
      <span className="cal-title">{ev.title}</span>
      {(ev.status === "live" || ev.status === "cancelled") && <span className="cal-state">{s.label}</span>}
    </button>
  );
}

// ------------------------------------------------------------------------------------------------ виды
function MonthView({ events, anchor, today, onOpen, onDay }: { events: CalEvent[]; anchor: Date; today: Date; onOpen: (e: CalEvent) => void; onDay: (d: Date) => void }) {
  const { from, to } = rangeFor("month", anchor);
  const days = daysOf(from, to);
  const by = useMemo(() => groupByDay(events, from, to), [events, from.getTime(), to.getTime()]);        // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="cal-month" role="grid" aria-label="Месяц">
      {WEEKDAYS.map((w) => <div key={w} className="cal-dow" role="columnheader">{w}</div>)}
      {days.map((d) => {
        const list = by.get(dayKey(d)) ?? [];
        return (
          <div key={dayKey(d)} role="gridcell" className={`cal-cell${d.getMonth() !== anchor.getMonth() ? " other" : ""}${sameDay(d, today) ? " today" : ""}`}>
            <button type="button" className="cal-daynum" onClick={() => onDay(d)} aria-label={`Открыть день: ${dayLabel(d)}`}>{d.getDate()}</button>
            {list.slice(0, 3).map((s) => <Chip key={`${s.ev.id}-${dayKey(d)}`} ev={s.ev} onOpen={onOpen} compact />)}
            {list.length > 3 && <button type="button" className="cal-more" onClick={() => onDay(d)}>ещё {list.length - 3}</button>}
          </div>
        );
      })}
    </div>
  );
}

function Grid({ events, from, to, today, onOpen, onDay }: { events: CalEvent[]; from: Date; to: Date; today: Date; onOpen: (e: CalEvent) => void; onDay?: (d: Date) => void }) {
  const days = daysOf(from, to);
  const by = useMemo(() => groupByDay(events, from, to), [events, from.getTime(), to.getTime()]);        // eslint-disable-line react-hooks/exhaustive-deps
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = Math.max(0, (new Date().getHours() - 2) * HOUR_PX); }, []);
  const nowPos = (new Date().getHours() * 60 + new Date().getMinutes()) / 1440;
  const allDay = days.map((d) => (by.get(dayKey(d)) ?? []).filter((s) => s.ev.all_day));
  return (
    <div className="cal-grid" style={{ ["--cols" as string]: days.length }}>
      <div className="cal-gh">
        <span />
        {days.map((d) => <button key={dayKey(d)} type="button" className={`cal-dayhead${sameDay(d, today) ? " today" : ""}`} onClick={() => onDay?.(d)} disabled={!onDay}>{WEEKDAYS[(d.getDay() + 6) % 7]} <b>{d.getDate()}</b></button>)}
      </div>
      {allDay.some((x) => x.length) && (
        <div className="cal-gh allday"><span className="cal-hour">весь день</span>{allDay.map((list, i) => <div key={i} className="cal-allcol">{list.map((s) => <Chip key={s.ev.id} ev={s.ev} onOpen={onOpen} compact />)}</div>)}</div>
      )}
      <div className="cal-scroll" ref={scroller}>
        <div className="cal-body" style={{ height: 24 * HOUR_PX }}>
          <div className="cal-hours" aria-hidden>{Array.from({ length: 24 }, (_, h) => <span key={h} style={{ top: h * HOUR_PX }}>{String(h).padStart(2, "0")}:00</span>)}</div>
          {days.map((d) => {
            const placed = layoutDay(by.get(dayKey(d)) ?? [], d);
            return (
              <div key={dayKey(d)} className={`cal-col${sameDay(d, today) ? " today" : ""}`} role="group" aria-label={dayLabel(d)}>
                {Array.from({ length: 24 }, (_, h) => <i key={h} className="cal-line" style={{ top: h * HOUR_PX }} />)}
                {sameDay(d, today) && <i className="cal-now" style={{ top: `${nowPos * 100}%` }} aria-hidden />}
                {placed.map((p) => (
                  <div key={`${p.slice.ev.id}-${p.col}`} className="cal-slot" style={{ top: `${p.top * 100}%`, height: `${p.height * 100}%`, left: `${(p.col / p.cols) * 100}%`, width: `${100 / p.cols}%` }}>
                    <Chip ev={p.slice.ev} onOpen={onOpen} />
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
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
              <span className="muted small">{KINDS[s.ev.kind].label}{s.ev.recurring ? " · повторяется" : ""}</span>
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

// ------------------------------------------------------------------------------------------------ создание встречи
type Attendee = CreateForm["attendees"][number];
const personKey = (a: Attendee) => a.id ?? (a.ref ? `ref:${a.ref}` : `mail:${a.email}`);

function CreateDialog({ status, initial, onClose, onCreated }: { status: CalendarStatus; initial: Date; onClose: () => void; onCreated: (c: CreatedEvent) => void }) {
  const [f, setF] = useState<CreateForm>(() => defaultForm(initial, status.guests_allowed));
  const [people, setPeople] = useState(false);
  const [mail, setMail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState<CreatedEvent | null>(null);
  const key = useRef(newKey());                    // один ключ на открытие формы: повторное нажатие «Создать» не создаёт вторую встречу
  const set = (p: Partial<CreateForm>) => setF((x) => ({ ...x, ...p }));
  const toggle = (p: Person) => setF((x) => {
    const a: Attendee = { id: p.id, ref: p.ref, login: p.login, name: p.name };
    const has = x.attendees.some((y) => personKey(y) === personKey(a));
    return { ...x, attendees: has ? x.attendees.filter((y) => personKey(y) !== personKey(a)) : [...x.attendees, a] };
  });
  const addMail = () => {
    const m = mail.trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(m)) { setErr("Введите адрес почты внешнего участника, например name@example.org."); return; }
    setErr("");
    setF((x) => x.attendees.some((y) => y.email === m) ? x : { ...x, attendees: [...x.attendees, { id: null, ref: null, login: null, name: m, email: m }] });
    setMail("");
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const bad = formError(f);
    if (bad) { setErr(bad); return; }
    setBusy(true); setErr("");
    try { const c = await calendarApi.create(buildCreateBody(f, key.current, localZone())); setDone(c); onCreated(c); }
    catch (x) { setErr(errText(x)); }
    finally { setBusy(false); }
  };
  const [toast, show] = useToast();
  if (done) {
    return (
      <Modal title="Встреча создана" onClose={onClose}>
        <div className="stack">
          <p>«{done.title}» появилась в вашем календаре Outlook. {f.attendees.length ? "Приглашения отправлены участникам." : "Участников пока нет."}</p>
          {done.skipped?.length ? <div className="alert warn">У этих сотрудников в профиле нет адреса почты, приглашение им не отправлено: {done.skipped.join(", ")}.</div> : null}
          {done.join_url && <div className="cal-link"><span>Для сотрудников</span><code>{done.join_url}</code><button className="btn mini" onClick={() => void copyText(done.join_url!).then((ok) => show(ok ? "Ссылка скопирована" : "Не удалось скопировать", ok ? "ok" : "error"))}>Копировать</button></div>}
          {done.guest_url && <div className="cal-link"><span>Для внешних участников</span><code>{done.guest_url}</code><button className="btn mini" onClick={() => void copyText(done.guest_url!).then((ok) => show(ok ? "Гостевая ссылка скопирована" : "Не удалось скопировать", ok ? "ok" : "error"))}>Копировать</button></div>}
          <div className="row"><button className="btn primary" onClick={onClose}>Готово</button></div>
        </div>
        {toast}
      </Modal>
    );
  }
  return (
    <Modal title="Новая встреча" onClose={onClose} wide>
      <form className="form cal-form" onSubmit={(e) => void submit(e)} noValidate>
        <label>Название<input value={f.title} maxLength={200} onChange={(e) => set({ title: e.target.value })} placeholder="Например: Согласование бюджета" /></label>
        <div className="cal-when">
          <label>Дата<input type="date" value={f.date} onChange={(e) => set({ date: e.target.value })} /></label>
          <label>Начало<input type="time" value={f.time} onChange={(e) => set({ time: e.target.value })} step={300} /></label>
          <label>Длительность<select value={f.minutes} onChange={(e) => set({ minutes: Number(e.target.value) })}>{DURATIONS.map((m) => <option key={m} value={m}>{durationText(m)}</option>)}</select></label>
        </div>
        <fieldset className="cal-repeat">
          <legend>Повторение</legend>
          <div className="row wrap">
            <select aria-label="Повторение" value={f.repeat} onChange={(e) => set({ repeat: e.target.value as CreateForm["repeat"] })}>{REPEATS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</select>
            {f.repeat === "weekly" && <span className="cal-days" role="group" aria-label="Дни недели">{WEEKDAYS.map((w, i) => <button key={w} type="button" className={`chip ${f.days.includes(i) ? "on" : ""}`} aria-pressed={f.days.includes(i)} onClick={() => set({ days: f.days.includes(i) ? f.days.filter((d) => d !== i) : [...f.days, i] })}>{w}</button>)}</span>}
            {f.repeat !== "none" && (
              <>
                <select aria-label="Окончание" value={f.endMode} onChange={(e) => set({ endMode: e.target.value as CreateForm["endMode"] })}><option value="never">Без окончания</option><option value="count">Повторить N раз</option><option value="date">До даты</option></select>
                {f.endMode === "count" && <input type="number" aria-label="Число повторений" min={1} max={999} value={f.count} onChange={(e) => set({ count: Number(e.target.value) })} style={{ width: 80 }} />}
                {f.endMode === "date" && <input type="date" aria-label="Повторять до" value={f.until} min={f.date} onChange={(e) => set({ until: e.target.value })} />}
              </>
            )}
          </div>
        </fieldset>
        <fieldset className="cal-who">
          <legend>Участники</legend>
          {f.attendees.length > 0 && <ul className="cal-people">{f.attendees.map((a) => <li key={personKey(a)}><span>{a.name}</span><button type="button" className="btn ghost mini" aria-label={`Убрать: ${a.name}`} onClick={() => set({ attendees: f.attendees.filter((y) => personKey(y) !== personKey(a)) })}><Icon name="close" size={12} /></button></li>)}</ul>}
          <div className="row wrap">
            <button type="button" className="btn mini" aria-expanded={people} onClick={() => setPeople((v) => !v)}><Icon name="users" size={14} /> {people ? "Скрыть поиск" : "Найти сотрудников"}</button>
            <input type="email" aria-label="Адрес внешнего участника" value={mail} onChange={(e) => setMail(e.target.value)} placeholder="или адрес почты внешнего участника" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addMail(); } }} style={{ flex: 1, minWidth: 220 }} />
            <button type="button" className="btn mini" onClick={addMail} disabled={!mail.trim()}>Добавить</button>
          </div>
          {people && <PeoplePicker selected={f.attendees.filter((a) => a.id || a.ref).map((a) => ({ id: a.id, ref: a.ref, login: a.login, name: a.name, source: a.id ? "local" : "directory" }))} onToggle={toggle} multi />}
        </fieldset>
        <label className="check"><input type="checkbox" checked={f.conference} onChange={(e) => set({ conference: e.target.checked })} /> Создать конференцию Peregovorka (ссылка попадёт в приглашение)</label>
        {status.guests_allowed && f.conference && (
          <label className="check"><input type="checkbox" checked={f.guest} onChange={(e) => set({ guest: e.target.checked })} /><span className="check-body">Добавить гостевую ссылку для внешних участников<span className="help">Гость входит без учётной записи и не видит историю и материалы организации.</span></span></label>
        )}
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Создаём…" : "Создать и разослать приглашения"}</button><button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Отмена</button></div>
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ карточка события
function EventDialog({ ev, status, onClose, onChanged }: { ev: CalEvent; status: CalendarStatus; onClose: () => void; onChanged: () => void }) {
  const navigate = useNavigate();
  const focusOnMount = useFocusOnMount();
  const [toast, show] = useToast();
  const [edit, setEdit] = useState(false);
  const [cancel, setCancel] = useState(false);
  const s0 = new Date(ev.start), e0 = new Date(ev.end);
  const [name, setName] = useState(ev.title);
  const [date, setDate] = useState(dateInput(s0));
  const [time, setTime] = useState(timeInput(s0));
  const [mins, setMins] = useState(Math.max(5, Math.round((e0.getTime() - s0.getTime()) / 60000)));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const k = KINDS[ev.kind], st = STATUSES[ev.status];
  const canJoin = !!ev.join_url && ev.status !== "cancelled" && ev.status !== "ended";
  const join = () => { const p = ev.join_url ? pathOf(ev.join_url) : null; if (p) navigate(p); else if (ev.join_url) window.location.assign(ev.join_url); };
  const copy = async (url: string, what: string) => show(`${what} ${(await copyText(url)) ? "скопирована" : "— скопировать не удалось"}`, "ok");
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const start = new Date(`${date}T${time}`);
    if (Number.isNaN(start.getTime()) || !name.trim()) { setErr("Укажите название, дату и время."); return; }
    setBusy(true); setErr("");
    try {
      await calendarApi.reschedule(ev.room_id!, { subject: name.trim(), start: start.toISOString(), end: new Date(start.getTime() + mins * 60000).toISOString(), etag: ev.etag });
      onChanged(); onClose();
    } catch (x) { setErr(errText(x)); setBusy(false); }
  };
  if (cancel) {
    return <ConfirmDialog title="Отменить встречу?" confirmLabel="Отменить встречу" onClose={() => setCancel(false)}
      body={<p>Встреча «{ev.title}» будет отменена в вашем календаре, участникам уйдёт уведомление об отмене, ссылки перестанут работать. Это действие нельзя отменить.</p>}
      onConfirm={async () => { await calendarApi.cancel(ev.room_id!); onChanged(); onClose(); }} />;
  }
  return (
    <Modal title={ev.title} onClose={onClose}>
      <div className="stack cal-detail">
        <div className="cal-badges"><span className={`cal-badge ${k.cls}`}><Icon name={k.icon} size={13} /> {k.label}</span><span className={`cal-badge ${st.cls}`}>{st.label}</span>{ev.recurring && <span className="cal-badge"><Icon name="retry" size={13} /> повторяется</span>}</div>
        {!edit ? (
          <>
            <p><b>{s0.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}</b>, {ev.all_day ? "весь день" : `${timeLabel(ev.start)}–${timeLabel(ev.end)}`}</p>
            {ev.location && <p className="muted">Место: {ev.location}</p>}
            {ev.source === "peregovorka" && <p className="muted small">Эта встреча есть в Peregovorka, но не найдена среди событий вашего календаря Outlook.</p>}
            {ev.join_url && <div className="cal-link"><span>Для сотрудников</span><code>{ev.join_url}</code><button className="btn mini" onClick={() => void copy(ev.join_url!, "Ссылка")}>Копировать</button></div>}
            {ev.guest_url && <div className="cal-link"><span>Для внешних участников</span><code>{ev.guest_url}</code><button className="btn mini" onClick={() => void copy(ev.guest_url!, "Гостевая ссылка")}>Копировать</button></div>}
            {ev.mine && ev.room_id && !ev.editable && ev.recurring && <p className="muted small">Серию встреч меняйте в Outlook: из Peregovorka изменяются только разовые встречи.</p>}
            <div className="row wrap">
              {canJoin && <button className="btn primary" onClick={join}><Icon name="video" size={15} /> Войти</button>}
              {ev.editable && status.can_write && <button className="btn" onClick={() => setEdit(true)}>Изменить время</button>}
              {ev.editable && status.can_write && <button className="btn danger ghost" onClick={() => setCancel(true)}>Отменить встречу</button>}
              <button className="btn ghost" onClick={onClose}>Закрыть</button>
            </div>
          </>
        ) : (
          <form className="form" ref={focusOnMount} onSubmit={(e) => void save(e)} noValidate>
            <label>Название<input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} /></label>
            <div className="cal-when">
              <label>Дата<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
              <label>Начало<input type="time" value={time} onChange={(e) => setTime(e.target.value)} /></label>
              <label>Длительность<select value={mins} onChange={(e) => setMins(Number(e.target.value))}>{[...new Set([...DURATIONS, mins])].sort((a, b) => a - b).map((m) => <option key={m} value={m}>{durationText(m)}</option>)}</select></label>
            </div>
            {err && <div className="alert error" role="alert">{err}</div>}
            <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить и уведомить участников"}</button><button type="button" className="btn ghost" onClick={() => setEdit(false)} disabled={busy}>Назад</button></div>
          </form>
        )}
      </div>
      {toast}
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ страница
export default function CalendarPage() {
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const [mode, setMode] = useState<ViewMode>(() => { try { const m = localStorage.getItem(MODE_KEY); return MODES.some((x) => x.id === m) ? (m as ViewMode) : "week"; } catch { return "week"; } });
  const [anchor, setAnchor] = useState(() => new Date());
  const [data, setData] = useState<CalendarEvents | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<CalEvent | null>(null);
  const [creating, setCreating] = useState(false);
  const seq = useRef(0);
  const [toast, show] = useToast();
  const today = new Date();
  const range = useMemo(() => rangeFor(mode, anchor), [mode, anchor.getFullYear(), anchor.getMonth(), anchor.getDate()]);        // eslint-disable-line react-hooks/exhaustive-deps

  const loadStatus = useCallback(() => calendarApi.status().then(setStatus).catch((e) => setErr(errText(e))), []);
  useEffect(() => { void loadStatus(); }, [loadStatus]);

  const load = useCallback(async (refresh = false) => {
    if (!status?.connected) return;
    const my = ++seq.current;                                                  // устаревший ответ (быстро переключили период) отбрасывается
    setLoading(true);
    try { const r = await calendarApi.events(range.from, range.to, refresh); if (my === seq.current) { setData(r); setErr(""); } }
    catch (e) { if (my === seq.current) setErr(errText(e)); }
    finally { if (my === seq.current) setLoading(false); }
  }, [status?.connected, range.from, range.to]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const t = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, REFRESH_MS);
    const vis = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [load]);

  const pickMode = (m: ViewMode) => { setMode(m); try { localStorage.setItem(MODE_KEY, m); } catch { /* запоминание вида необязательно */ } };
  const toDay = (d: Date) => { setAnchor(d); pickMode("day"); };

  if (!status) return <section className="cal-page"><h1>Календарь</h1>{err ? <div className="alert error" role="alert">{err}</div> : <p className="muted">Загрузка…</p>}</section>;
  if (!status.available) {
    return <section className="cal-page"><h1>Календарь</h1><div className="alert info" role="status">{status.message}</div></section>;
  }
  if (!status.connected) return <section className="cal-page"><h1>Календарь</h1><Connect status={status} onDone={(s) => { setStatus(s); show("Календарь подключён", "ok"); }} /></section>;

  const events = data?.events ?? [];
  return (
    <section className="cal-page">
      <header className="cal-head">
        <h1>Календарь</h1>
        {status.can_write && <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" size={15} /> Новая встреча</button>}
        <div className="cal-nav" role="group" aria-label="Период">
          <button className="btn" onClick={() => setAnchor(new Date())}>Сегодня</button>
          <button className="btn icon" aria-label="Назад" title="Назад" onClick={() => setAnchor(shift(mode, anchor, -1))}><Icon name="chevronL" size={16} /></button>
          <button className="btn icon" aria-label="Вперёд" title="Вперёд" onClick={() => setAnchor(shift(mode, anchor, 1))}><Icon name="chevronR" size={16} /></button>
          <label className="cal-jump"><span className="sr-only">Перейти к дате</span><input type="date" value={dateInput(anchor)} onChange={(e) => { if (e.target.value) setAnchor(new Date(`${e.target.value}T12:00`)); }} aria-label="Перейти к дате" /></label>
        </div>
        <h2 className="cal-period" aria-live="polite">{periodTitle(mode, anchor)}</h2>
        <div className="spacer" />
        <div className="seg" role="group" aria-label="Вид">{MODES.map((m) => <button key={m.id} type="button" aria-pressed={mode === m.id} onClick={() => pickMode(m.id)}>{m.label}</button>)}</div>
        <button className="btn" onClick={() => void load(true)} disabled={loading} title="Обновить из Exchange"><Icon name="retry" size={15} /> {loading ? "Обновляем…" : "Обновить"}</button>
      </header>
      {(err || data?.error) && <div className="alert warn" role="status">{data?.stale ? "Exchange сейчас недоступен — показаны данные, которые могли устареть. " : ""}{err || data?.error}</div>}
      <ul className="cal-legend" aria-label="Обозначения">
        {(Object.keys(KINDS) as (keyof typeof KINDS)[]).map((k) => <li key={k} className={KINDS[k].cls} title={KINDS[k].hint}><Icon name={KINDS[k].icon} size={13} /> {KINDS[k].label}</li>)}
        <li className="s-live"><span className="dot" aria-hidden /> Идёт</li><li className="s-cancelled"><s>Отменена</s></li>
      </ul>
      <div className="cal-view" aria-busy={loading}>
        {mode === "month" && <MonthView events={events} anchor={anchor} today={today} onOpen={setOpen} onDay={toDay} />}
        {mode === "week" && <Grid events={events} from={range.from} to={range.to} today={today} onOpen={setOpen} onDay={toDay} />}
        {mode === "day" && <Grid events={events} from={range.from} to={range.to} today={today} onOpen={setOpen} />}
        {mode === "list" && <ListView events={events} from={range.from} to={range.to} onOpen={setOpen} />}
      </div>
      <footer className="cal-foot muted small">
        Календарь ящика {status.email}. Время показано в вашем часовом поясе ({localZone()}). {status.can_write ? "" : "Только просмотр. "}
        <button className="linkish" onClick={async () => { try { setStatus(await calendarApi.disconnect()); setData(null); } catch (e) { setErr(errText(e)); } }}>Отключить календарь</button>
      </footer>
      {open && <EventDialog ev={open} status={status} onClose={() => setOpen(null)} onChanged={() => void load(true)} />}
      {creating && <CreateDialog status={status} initial={nextSlot(new Date())} onClose={() => setCreating(false)} onCreated={() => void load(true)} />}
      {toast}
    </section>
  );
}
