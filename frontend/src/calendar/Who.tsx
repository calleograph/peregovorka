import { useState } from "react";
import { PeoplePicker } from "../chat/ChatDialogs";
import type { Person } from "../chat/chatApi";
import { personKey, type AttendeeRow } from "../calendarView";
import { Icon } from "../components/Icons";

/** Участники встречи: сотрудники из каталога и внешние адреса. Один компонент для создания и изменения. */
export function Who({ list, onChange, onError }: { list: AttendeeRow[]; onChange: (l: AttendeeRow[]) => void; onError: (m: string) => void }) {
  const [people, setPeople] = useState(false);
  const [mail, setMail] = useState("");
  const toggle = (p: Person) => {
    const a: AttendeeRow = { id: p.id, ref: p.ref, login: p.login, name: p.name };
    onChange(list.some((y) => personKey(y) === personKey(a)) ? list.filter((y) => personKey(y) !== personKey(a)) : [...list, a]);
  };
  const addMail = () => {
    const m = mail.trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(m)) { onError("Введите адрес почты внешнего участника, например name@example.org."); return; }
    onError("");
    if (!list.some((y) => y.email === m)) onChange([...list, { id: null, ref: null, login: null, name: m, email: m }]);
    setMail("");
  };
  return (
    <fieldset className="cal-who">
      <legend>Участники</legend>
      {list.length > 0 && (
        <ul className="cal-people">{list.map((a) => (
          <li key={personKey(a)} title={a.email}><span>{a.name}</span>
            <button type="button" className="btn ghost mini" aria-label={`Убрать: ${a.name}`} onClick={() => onChange(list.filter((y) => personKey(y) !== personKey(a)))}><Icon name="close" size={12} /></button></li>
        ))}</ul>
      )}
      <div className="row wrap">
        <button type="button" className="btn mini" aria-expanded={people} onClick={() => setPeople((v) => !v)}><Icon name="users" size={14} /> {people ? "Скрыть поиск" : "Найти сотрудников"}</button>
        <input type="email" aria-label="Адрес внешнего участника" value={mail} onChange={(e) => setMail(e.target.value)} placeholder="или адрес почты внешнего участника"
               onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addMail(); } }} style={{ flex: 1, minWidth: 220 }} />
        <button type="button" className="btn mini" onClick={addMail} disabled={!mail.trim()}>Добавить</button>
      </div>
      {people && <PeoplePicker selected={list.filter((a) => a.id || a.ref).map((a) => ({ id: a.id, ref: a.ref, login: a.login, name: a.name, source: a.id ? "local" : "directory" }))} onToggle={toggle} multi />}
    </fieldset>
  );
}
