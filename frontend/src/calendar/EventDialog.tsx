import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { calendarApi, type CalendarStatus, type EventCard, type Scope } from "../calendarApi";
import {
  attendeeBody, dateInput, DURATIONS, durationText, isConference, personKey, SHOW_AS, STATUSES, timeInput, timeLabel, toneOf, TONES, type AttendeeRow, type CalEvent,
} from "../calendarView";
import { Modal } from "../components/Dialogs";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { copyText } from "../util";
import { useFocusOnMount } from "../useInitialFocus";
import { localZone } from "../calendarView";
import { errText, pathOf } from "./common";
import { Who } from "./Who";

const RESPONSE: Record<string, string> = {
  accept: "принял(а)", accepted: "принял(а)", decline: "отклонил(а)", declined: "отклонил(а)", tentative: "под вопросом", tentativelyaccepted: "под вопросом",
  organizer: "организатор", noresponsereceived: "не ответил(а)", notresponded: "не ответил(а)", none: "не ответил(а)", unknown: "не ответил(а)",
};
const MAX_SHOWN = 12;

/** Выбор для занятия серии: «только это занятие» или «вся серия». По умолчанию — безопасное «только это», но выбор всегда виден. */
function ScopePick({ value, onChange, what }: { value: Scope; onChange: (s: Scope) => void; what: string }) {
  return (
    <fieldset className="cal-scope">
      <legend>Это занятие повторяющейся встречи. Что {what}?</legend>
      <label className="check"><input type="radio" name="scope" checked={value === "occurrence"} onChange={() => onChange("occurrence")} /> Только это занятие</label>
      <label className="check"><input type="radio" name="scope" checked={value === "series"} onChange={() => onChange("series")} /> Всю серию</label>
    </fieldset>
  );
}

/** Карточка события: сведения из Exchange (организатор, участники, место, описание), вход в конференцию, изменение и отмена. */
export function EventDialog({ ev, status, onClose, onChanged }: { ev: CalEvent; status: CalendarStatus; onClose: () => void; onChanged: () => void }) {
  const navigate = useNavigate();
  const focusOnMount = useFocusOnMount();
  const [toast, show] = useToast();
  const [card, setCard] = useState<EventCard | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [mode, setMode] = useState<"view" | "edit" | "cancel">("view");
  const fromExchange = ev.source === "exchange";
  useEffect(() => {
    if (!fromExchange) return;
    let alive = true;
    calendarApi.open(ev.id).then((c) => { if (alive) setCard(c); }).catch((e) => { if (alive) setLoadErr(errText(e)); });
    return () => { alive = false; };
  }, [ev.id, fromExchange]);

  const cur: CalEvent = card ? { ...card, organizer: card.organizer?.email ?? null } : ev;
  const s0 = new Date(cur.start), e0 = new Date(cur.end);
  const minutes = Math.max(5, Math.round((e0.getTime() - s0.getTime()) / 60000));
  const tone = toneOf(cur), conf = isConference(cur), st = STATUSES[cur.status];
  const occurrence = cur.event_kind === "occurrence" || cur.event_kind === "exception";
  const canEdit = !!(card ? card.can_edit : false) && status.can_write;
  const canJoin = !!cur.join_url && cur.status !== "cancelled" && cur.status !== "ended";
  const join = () => { const p = cur.join_url ? pathOf(cur.join_url) : null; if (p) navigate(p); else if (cur.join_url) window.location.assign(cur.join_url); };
  const copy = async (url: string, what: string) => show(`${what} ${(await copyText(url)) ? "скопирована" : "— скопировать не удалось"}`, "ok");

  // ----- изменение
  const initialPeople = useMemo<AttendeeRow[]>(() => (card?.attendees ?? []).map((a) => ({ id: null, ref: null, login: null, name: a.name || a.email, email: a.email, optional: a.type === "optional" })), [card]);
  const [name, setName] = useState(ev.title);
  const [date, setDate] = useState(dateInput(s0));
  const [time, setTime] = useState(timeInput(s0));
  const [mins, setMins] = useState(minutes);
  const [place, setPlace] = useState(ev.location ?? "");
  const [people, setPeople] = useState<AttendeeRow[] | null>(null);
  const [scope, setScope] = useState<Scope>("occurrence");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const startEdit = () => { setName(cur.title); setDate(dateInput(s0)); setTime(timeInput(s0)); setMins(minutes); setPlace(cur.location ?? ""); setPeople(initialPeople); setErr(""); setMode("edit"); };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const start = new Date(`${date}T${time}`);
    if (Number.isNaN(start.getTime()) || !name.trim()) { setErr("Укажите название, дату и время."); return; }
    const body: Record<string, unknown> = { id: ev.id, tz: localZone() };
    if (occurrence) body.scope = scope;
    if (name.trim() !== cur.title) body.subject = name.trim();
    if (start.getTime() !== s0.getTime() || mins !== minutes) { body.start = start.toISOString(); body.end = new Date(start.getTime() + mins * 60000).toISOString(); }
    if (place.trim() !== (cur.location ?? "")) body.location = place.trim();
    const cl = people ?? initialPeople;
    const sig = (l: AttendeeRow[]) => l.map((a) => personKey(a) + (a.optional ? "|o" : "|r")).sort().join(",");
    if (sig(cl) !== sig(initialPeople)) body.attendees = attendeeBody(cl);
    if (Object.keys(body).every((k) => k === "id" || k === "tz" || k === "scope")) { setErr("Ничего не изменено."); return; }
    setBusy(true); setErr("");
    try { await calendarApi.update(body); onChanged(); onClose(); }
    catch (x) { setErr(errText(x)); setBusy(false); }
  };
  const doCancel = async () => {
    setBusy(true); setErr("");
    try { await calendarApi.cancelEvent({ id: ev.id, ...(occurrence ? { scope } : {}) }); onChanged(); onClose(); }
    catch (x) { setErr(errText(x)); setBusy(false); }
  };

  const shown = (card?.attendees ?? []).slice(0, MAX_SHOWN);
  return (
    <Modal title={cur.title} onClose={onClose} wide={mode === "edit"}>
      <div className="stack cal-detail">
        <div className="cal-badges">
          <span className={`cal-badge ${TONES[tone].cls}`}>{TONES[tone].label}</span>
          {conf && <span className="cal-badge conf"><Icon name="video" size={13} /> Конференция Peregovorka</span>}
          {cur.recurring && <span className="cal-badge"><Icon name="retry" size={13} /> {occurrence ? "Занятие серии" : "Повторяется"}</span>}
          <span className={`cal-badge ${st.cls}`}>{st.label}</span>
          {cur.show_as && cur.show_as !== "busy" && <span className="cal-badge">{SHOW_AS[cur.show_as]}</span>}
        </div>
        {fromExchange && !card && !loadErr && <p className="muted small" role="status">Загружаем сведения из Exchange…</p>}
        {loadErr && <div className="alert warn" role="status">Подробности из Exchange получить не удалось: {loadErr} Показаны сведения из списка; изменить событие сейчас нельзя.</div>}
        {mode === "view" && (
          <>
            <p><b>{s0.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}</b>, {cur.all_day ? "весь день" : `${timeLabel(cur.start)}–${timeLabel(cur.end)} · ${durationText(minutes)}`}</p>
            {card?.organizer && <p className="muted">Организатор: {card.organizer_is_me ? "вы" : `${card.organizer.name ? `${card.organizer.name}, ` : ""}${card.organizer.email}`}</p>}
            {cur.location && <p className="muted">Место: {cur.location}</p>}
            {cur.source === "peregovorka" && <p className="muted small">Эта встреча есть в Peregovorka, но не найдена среди событий вашего календаря Outlook.</p>}
            {card && card.attendees.length > 0 && (
              <div className="cal-att">
                <h3>Участники <span className="muted">{card.attendees.length}</span></h3>
                <ul>{shown.map((a) => <li key={a.email} title={a.email}><span className="cal-attname">{a.name || a.email}</span>{a.type === "optional" && <span className="muted small"> · необязательный</span>}<span className="muted small"> · {RESPONSE[a.response] ?? "не ответил(а)"}</span></li>)}</ul>
                {card.attendees.length > MAX_SHOWN && <p className="muted small">и ещё {card.attendees.length - MAX_SHOWN}</p>}
              </div>
            )}
            {card?.description && <div className="cal-desc" tabIndex={0} aria-label="Описание">{card.description}</div>}
            {cur.join_url && <div className="cal-link"><span>Для сотрудников</span><code>{cur.join_url}</code><button className="btn mini" onClick={() => void copy(cur.join_url!, "Ссылка")}>Копировать</button></div>}
            {cur.guest_url && <div className="cal-link"><span>Для внешних участников</span><code>{cur.guest_url}</code><button className="btn mini" onClick={() => void copy(cur.guest_url!, "Гостевая ссылка")}>Копировать</button></div>}
            {card && !canEdit && cur.status !== "cancelled" && cur.status !== "ended" && (
              <p className="muted small">{card.organizer_is_me ? (status.can_write ? "" : "Изменение встреч из Peregovorka администратор не включил.") : "Изменять и отменять встречу может её организатор: вы приглашены."}</p>
            )}
            <div className="row wrap">
              {canJoin && <button className="btn primary" onClick={join}><Icon name="video" size={15} /> Войти</button>}
              {canEdit && <button className="btn" onClick={startEdit}><Icon name="edit" size={15} /> Изменить</button>}
              {canEdit && <button className="btn danger ghost" onClick={() => { setScope("occurrence"); setErr(""); setMode("cancel"); }}>Отменить встречу</button>}
              <button className="btn ghost" onClick={onClose}>Закрыть</button>
            </div>
          </>
        )}
        {mode === "edit" && (
          <form className="form cal-form" ref={focusOnMount} onSubmit={(e) => void save(e)} noValidate>
            <label>Название<input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} /></label>
            <div className="cal-when">
              <label>Дата<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
              <label>Начало<input type="time" value={time} onChange={(e) => setTime(e.target.value)} step={300} /></label>
              <label>Длительность<select value={mins} onChange={(e) => setMins(Number(e.target.value))}>{[...new Set([...DURATIONS, mins])].sort((a, b) => a - b).map((m) => <option key={m} value={m}>{durationText(m)}</option>)}</select></label>
            </div>
            <label>Место<input value={place} maxLength={300} onChange={(e) => setPlace(e.target.value)} /></label>
            <Who list={people ?? initialPeople} onChange={setPeople} onError={setErr} />
            {occurrence && <ScopePick value={scope} onChange={setScope} what="изменить" />}
            {occurrence && scope === "series" && <p className="muted small">В серии изменятся время суток и длительность всех занятий, дата начала серии останется прежней.</p>}
            {err && <div className="alert error" role="alert">{err}</div>}
            <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить и уведомить участников"}</button><button type="button" className="btn ghost" onClick={() => setMode("view")} disabled={busy}>Назад</button></div>
          </form>
        )}
        {mode === "cancel" && (
          <div className="stack">
            <p>Встреча «{cur.title}» будет отменена в вашем календаре, участникам уйдёт уведомление об отмене{conf ? ", ссылки Peregovorka перестанут работать" : ""}. Это действие нельзя отменить.</p>
            {occurrence && <ScopePick value={scope} onChange={setScope} what="отменить" />}
            {err && <div className="alert error" role="alert">{err}</div>}
            <div className="row">
              <button className="btn danger" onClick={() => void doCancel()} disabled={busy}>{busy ? "Отменяем…" : occurrence && scope === "series" ? "Отменить всю серию" : "Отменить встречу"}</button>
              <button className="btn ghost" onClick={() => setMode("view")} disabled={busy}>Не отменять</button>
            </div>
          </div>
        )}
      </div>
      {toast}
    </Modal>
  );
}
