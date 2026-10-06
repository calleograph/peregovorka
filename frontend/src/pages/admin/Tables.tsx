import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AdminUser, type ApiError, type AuditRow, type Meeting, type RecordingRow } from "../../api";
import MeetingAdminActions from "../../components/MeetingAdminActions";
import { ConfirmDialog } from "../../components/Dialogs";
import { bytes, fmt } from "../../util";

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
      <p className="muted">Локальное представление доменных пользователей: они появляются после первого входа. Отключённый пользователь теряет сессию и не может войти, пока не включён снова. Пароли в системе не хранятся. Права администратора определяются членством в группе AD (LDAP_ADMIN_GROUP_DN).</p>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по имени или логину, например: иванов" style={{ maxWidth: 360 }} aria-label="Поиск пользователя" />
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
  const [forceEnd, setForceEnd] = useState<Meeting | null>(null);
  const load = useCallback(() => api.admin.meetings(active ? true : undefined).then(setRows).catch((e) => setErr(e.message)), [active]);
  useEffect(() => { void load(); const t = window.setInterval(load, 10000); return () => window.clearInterval(t); }, [load]);
  return (
    <section>
      <div className="row"><h2>Встречи</h2><div className="spacer" />
        <label className="check"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Только идущие</label></div>
      <p className="muted">Идущие встречи можно завершить принудительно (все участники будут отключены). Для завершённых доступны материалы, выдача доступа и удаление: отдельно только аудио или встречи целиком — с подтверждением и записью в аудит.</p>
      {err && <div className="alert error">{err}</div>}
      <table className="table">
        <thead><tr><th>Комната</th><th>Начало</th><th>Окончание</th><th>Участники</th><th>Материалы</th><th /></tr></thead>
        <tbody>{rows.map((m) => (
          <tr key={m.id}>
            <td>{m.room_name}</td><td>{fmt(m.started_at)}</td><td>{m.ended_at ? `${fmt(m.ended_at)} (${m.end_reason ?? ""})` : <span className="badge rec">идёт</span>}</td>
            <td>{m.participants.filter((p) => p.online || m.ended_at).map((p) => p.display_name).join(", ")}</td>
            <td className="small muted">реплик {m.segments} · документов {m.protocols} · записей {m.recordings}</td>
            <td className="actions">
              {!m.ended_at && <button className="btn mini ghost danger" onClick={() => setForceEnd(m)}>Завершить</button>}{" "}
              <Link className="btn mini" to={`/history/${m.id}`}>Открыть</Link>{" "}
              {m.ended_at && <MeetingAdminActions meeting={m} onChanged={load} />}
            </td>
          </tr>))}</tbody>
      </table>
      {forceEnd && (
        <ConfirmDialog title="Завершить встречу принудительно?" confirmLabel="Завершить" onClose={() => setForceEnd(null)}
          body={<p>Встреча в «{forceEnd.room_name}» будет завершена, все участники отключены. Действие записывается в журнал аудита.</p>}
          onConfirm={async () => { await api.admin.endMeeting(forceEnd.id); await load(); }} />
      )}
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
      <p className="muted">Кто, что и когда сделал: изменения настроек, удаления записей, протоколов и встреч, выдача доступа, скачивание аудио. Хранится отдельно от технического журнала; пароли и секреты в него не попадают.</p>
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

const EXPORT_LABEL: Record<string, string> = { exported: "выгружена", pending: "ожидает выгрузки", failed: "ошибка выгрузки", disabled: "хранилище выключено", local: "локально" };

export function RecordingsAdmin() {
  const [rows, setRows] = useState<RecordingRow[]>([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.admin.recordings().then(setRows).catch((e) => setErr(e.message)), []);
  useEffect(() => { void load(); }, [load]);
  const failed = rows.filter((r) => r.export_status === "failed" || r.export_status === "pending").length;
  const retry = async () => {
    setBusy(true); setErr(""); setNote("");
    try { const r = await api.admin.retryExports(); setNote(`Выгружено: ${r.exported}, всё ещё с ошибкой: ${r.still_failed}`); await load(); }
    catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  return (
    <section>
      <div className="row"><h2>Записи аудио</h2><div className="spacer" />
        <button className="btn" onClick={retry} disabled={busy}>Повторить выгрузку записей{failed ? ` (${failed})` : ""}</button></div>
      <p className="muted">Файлы по участникам (WAV, 16 кГц). Срок хранения задаётся в настройках комнаты, внешнее хранилище — в разделе «Хранилище записей». Если хранилище было недоступно, запись не теряется: она остаётся локально и выгружается повторно. Скачивание доступно только администраторам и пишется в аудит.</p>
      {err && <div className="alert error">{err}</div>}
      {note && <div className="alert ok">{note}</div>}
      <table className="table">
        <thead><tr><th>Комната</th><th>Дата</th><th>Файл</th><th>Длительность</th><th>Размер</th><th>Хранилище</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id}><td>{r.room}</td><td>{fmt(r.created_at)}</td><td>{r.path.split("/").pop()}</td><td>{r.duration_s ? `${Math.floor(r.duration_s / 60)}:${String(r.duration_s % 60).padStart(2, "0")}` : "—"}</td>
            <td>{bytes(r.size_bytes)}</td>
            <td>{r.export_status === "failed" ? <span className="badge warn" title={r.export_error ?? ""}>{EXPORT_LABEL.failed}</span> : EXPORT_LABEL[r.export_status ?? ""] ?? r.export_status ?? "—"}
              {r.export_error && <div className="small muted">{r.export_error}</div>}</td>
            <td><a href={`/api/v1/meetings/${r.meeting_id}/recordings/${r.id}`} download>Скачать</a></td></tr>))}</tbody>
      </table>
    </section>
  );
}
