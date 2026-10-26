import { useRef, useState, type FormEvent } from "react";
import { calendarApi, type CalendarStatus, type CreatedEvent } from "../calendarApi";
import { buildCreateBody, defaultForm, DURATIONS, durationText, formError, localZone, newKey, REPEATS, WEEKDAYS, type CreateForm } from "../calendarView";
import { Modal } from "../components/Dialogs";
import { useToast } from "../components/Toast";
import { copyText } from "../util";
import { errText } from "./common";
import { Who } from "./Who";

/** Новая встреча: событие создаётся в календаре самого сотрудника, приглашения рассылает Exchange. Курсор сразу в названии (Modal ставит фокус в первое поле). */
export function CreateDialog({ status, initial, minutes, onClose, onCreated }: { status: CalendarStatus; initial: Date; minutes?: number; onClose: () => void; onCreated: (c: CreatedEvent) => void }) {
  const [f, setF] = useState<CreateForm>(() => defaultForm(initial, status.guests_allowed, minutes));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState<CreatedEvent | null>(null);
  const key = useRef(newKey());                    // один ключ на открытие формы: повторное нажатие «Создать» не создаёт вторую встречу
  const set = (p: Partial<CreateForm>) => setF((x) => ({ ...x, ...p }));
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
  const copy = (url: string, what: string) => void copyText(url).then((ok) => show(ok ? `${what} скопирована` : "Не удалось скопировать", ok ? "ok" : "error"));
  if (done) {
    return (
      <Modal title="Встреча создана" onClose={onClose}>
        <div className="stack">
          <p>«{done.title}» появилась в вашем календаре Outlook. {f.attendees.length ? "Приглашения отправлены участникам." : "Участников пока нет."}</p>
          {done.skipped?.length ? <div className="alert warn">У этих сотрудников в профиле нет адреса почты, приглашение им не отправлено: {done.skipped.join(", ")}.</div> : null}
          {done.join_url && <div className="cal-link"><span>Для сотрудников</span><code>{done.join_url}</code><button className="btn mini" onClick={() => copy(done.join_url!, "Ссылка")}>Копировать</button></div>}
          {done.guest_url && <div className="cal-link"><span>Для внешних участников</span><code>{done.guest_url}</code><button className="btn mini" onClick={() => copy(done.guest_url!, "Гостевая ссылка")}>Копировать</button></div>}
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
          <label>Длительность<select value={f.minutes} onChange={(e) => set({ minutes: Number(e.target.value) })}>{[...new Set([...DURATIONS, f.minutes])].sort((a, b) => a - b).map((m) => <option key={m} value={m}>{durationText(m)}</option>)}</select></label>
        </div>
        <label>Место<input value={f.location} maxLength={300} onChange={(e) => set({ location: e.target.value })} placeholder="Переговорная, адрес или оставьте пустым" /></label>
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
        <Who list={f.attendees} onChange={(attendees) => set({ attendees })} onError={setErr} />
        <label>Описание<textarea value={f.description} maxLength={4000} rows={3} onChange={(e) => set({ description: e.target.value })} placeholder="Повестка или пояснение для участников (необязательно)" /></label>
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
