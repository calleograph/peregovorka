import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AdminUser, type ApiError, type AuditRow, type Meeting, type RecordingRow, type SystemStatus } from "../../api";
import { fmt } from "../HistoryPage";

const bytes = (n: number | null | undefined) => {
  if (n == null) return "—";
  const u = ["Б", "КБ", "МБ", "ГБ", "ТБ"]; let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
};

export function UsersAdmin() {
  const [rows, setRows] = useState<AdminUser[]>([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.users(q).then(setRows).catch((e) => setErr(e.message)), [q]);
  useEffect(() => { const t = window.setTimeout(load, 250); return () => window.clearTimeout(t); }, [load]);
  const toggle = async (u: AdminUser) => {
    setErr("");
    try { await api.admin.setUserActive(u.id, !u.is_active); await load(); } catch (e) { setErr((e as ApiError).message); }
  };
  return (
    <section>
      <h2>Пользователи</h2>
      <p className="muted">Локальное представление доменных пользователей (появляются после первого входа). Отключённый пользователь теряет сессию и не может войти, пока не включён снова. Пароли в системе не хранятся.</p>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по имени или логину" style={{ maxWidth: 360 }} />
      {err && <div className="alert error">{err}</div>}
      <table className="table">
        <thead><tr><th>Имя</th><th>Логин</th><th>Почта</th><th>Последний вход</th><th>Статус</th><th /></tr></thead>
        <tbody>{rows.map((u) => (
          <tr key={u.id}>
            <td>{u.display_name}{u.is_admin && <span className="badge"> админ</span>}</td><td>{u.sam_account_name}</td><td>{u.email ?? "—"}</td>
            <td>{u.last_login_at ? fmt(u.last_login_at) : "—"}</td>
            <td>{u.is_active ? "активен" : <span className="badge warn">отключён</span>}</td>
            <td><button className={`btn ghost ${u.is_active ? "danger" : ""}`} onClick={() => toggle(u)}>{u.is_active ? "Отключить" : "Включить"}</button></td>
          </tr>))}</tbody>
      </table>
    </section>
  );
}

export function MeetingsAdmin() {
  const [rows, setRows] = useState<Meeting[]>([]);
  const [active, setActive] = useState(true);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.meetings(active ? true : undefined).then(setRows).catch((e) => setErr(e.message)), [active]);
  useEffect(() => { void load(); const t = window.setInterval(load, 10000); return () => window.clearInterval(t); }, [load]);
  const end = async (m: Meeting) => {
    if (!window.confirm(`Принудительно завершить встречу в «${m.room_name}»? Все участники будут отключены.`)) return;
    try { await api.admin.endMeeting(m.id); await load(); } catch (e) { setErr((e as ApiError).message); }
  };
  return (
    <section>
      <div className="row"><h2>Встречи</h2><div className="spacer" />
        <label className="check"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Только идущие</label></div>
      {err && <div className="alert error">{err}</div>}
      <table className="table">
        <thead><tr><th>Комната</th><th>Начало</th><th>Окончание</th><th>Участники</th><th /></tr></thead>
        <tbody>{rows.map((m) => (
          <tr key={m.id}>
            <td>{m.room_name}</td><td>{fmt(m.started_at)}</td><td>{m.ended_at ? `${fmt(m.ended_at)} (${m.end_reason ?? ""})` : <span className="badge rec">идёт</span>}</td>
            <td>{m.participants.filter((p) => p.online || m.ended_at).map((p) => p.display_name).join(", ")}</td>
            <td className="actions">{!m.ended_at && <button className="btn ghost danger" onClick={() => end(m)}>Завершить</button>}
              <Link to={`/history/${m.id}`}>Протокол</Link></td>
          </tr>))}</tbody>
      </table>
    </section>
  );
}

export function AuditAdmin() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [offset, setOffset] = useState(0);
  const [err, setErr] = useState("");
  useEffect(() => { api.admin.audit(offset).then(setRows).catch((e) => setErr(e.message)); }, [offset]);
  return (
    <section>
      <h2>Журнал аудита</h2>
      <p className="muted">Административные действия (отдельно от технического журнала). Пароли и секреты в журнал не попадают.</p>
      {err && <div className="alert error">{err}</div>}
      <table className="table">
        <thead><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Подробности</th><th>IP</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id}><td>{fmt(r.at)}</td><td>{r.actor}</td><td><code>{r.action}</code></td><td>{r.target_type} <span className="muted small">{r.target_id.slice(0, 8)}</span></td>
            <td className="small">{r.details ? JSON.stringify(r.details).slice(0, 200) : ""}</td><td className="small">{r.ip ?? ""}</td></tr>))}</tbody>
      </table>
      <div className="row"><button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>← Новее</button>
        <button className="btn" disabled={rows.length < 100} onClick={() => setOffset(offset + 100)}>Старее →</button></div>
    </section>
  );
}

export function RecordingsAdmin() {
  const [rows, setRows] = useState<RecordingRow[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => { api.admin.recordings().then(setRows).catch((e) => setErr(e.message)); }, []);
  return (
    <section>
      <h2>Записи аудио</h2>
      <p className="muted">Файлы по участникам (WAV, 16 кГц). Срок хранения задаётся в настройках комнаты. Скачивание доступно только администраторам и пишется в аудит.</p>
      {err && <div className="alert error">{err}</div>}
      <table className="table">
        <thead><tr><th>Комната</th><th>Дата</th><th>Файл</th><th>Длительность</th><th>Размер</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id}><td>{r.room}</td><td>{fmt(r.created_at)}</td><td>{r.path.split("/").pop()}</td><td>{r.duration_s ? `${Math.floor(r.duration_s / 60)}:${String(r.duration_s % 60).padStart(2, "0")}` : "—"}</td>
            <td>{bytes(r.size_bytes)}</td><td><a href={`/api/v1/meetings/${r.meeting_id}/recordings/${r.id}`}>Скачать</a></td></tr>))}</tbody>
      </table>
    </section>
  );
}

export function SystemAdmin() {
  const [s, setS] = useState<SystemStatus | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.system().then(setS).catch((e) => setErr(e.message)), []);
  useEffect(() => { void load(); const t = window.setInterval(load, 15000); return () => window.clearInterval(t); }, [load]);
  if (err) return <div className="alert error">{err}</div>;
  if (!s) return <div className="muted">Загрузка…</div>;
  const names: Record<string, string> = { postgres: "PostgreSQL", redis: "Redis", livekit: "LiveKit", asr: "ASR (транскрибация)", ldap: "Active Directory (LDAPS)" };
  return (
    <section>
      <h2>Состояние системы</h2>
      <p>Версия <b>{s.version}</b> · commit <code>{s.commit.slice(0, 12)}</code> · {s.public_url}</p>
      {!s.master_key_ok && <div className="alert error">APP_MASTER_KEY не задан или некорректен — секретные настройки (пароли, токены, ключи) сохранить нельзя.</div>}
      <div className="grid">
        {Object.entries(s.checks).map(([k, v]) => (
          <div key={k} className="card"><div className="row"><span className={`dot ${v.ok ? "ok" : "bad"}`} /><b>{names[k] ?? k}</b></div>
            <div className="muted small">{v.ok ? "работает" : `недоступен${v.error ? ` (${v.error})` : ""}`}</div>
            {k === "asr" && v.ok && <div className="muted small">очередь {String(v.queue_depth)} · обработано {String(v.processed)} · отброшено {String(v.dropped)} · ошибок {String(v.errors)}</div>}
          </div>))}
      </div>
      <h3>Данные</h3>
      <table className="table"><tbody>
        {Object.entries({ "Пользователей": s.counts.users, "Комнат": s.counts.rooms, "Встреч": s.counts.meetings, "Идёт сейчас": s.counts.active_meetings,
          "Реплик": s.counts.segments, "Записей аудио": s.counts.recordings, "Объём записей": bytes(s.counts.recordings_bytes), "Протоколов": s.counts.protocols,
          "Свободно на диске данных": bytes(s.disk_free_bytes) }).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}
      </tbody></table>
    </section>
  );
}
