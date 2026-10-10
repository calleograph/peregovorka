import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AdminUser, type ApiError, type AuditRow, type Meeting, type RecordingRow } from "../../api";
import MeetingAdminActions from "../../components/MeetingAdminActions";
import { ConfirmDialog } from "../../components/Dialogs";
import ClientDiagAdmin from "./ClientDiagAdmin";
import { ListFooter, useInfinite } from "../../useInfinite";
import { bytes, fmt } from "../../util";

export function UsersAdmin() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { const t = window.setTimeout(() => setQuery(q), 250); return () => window.clearTimeout(t); }, [q]);
  const list = useInfinite<AdminUser>(async (offset) => { const r = await api.admin.users(query, offset); return { rows: r, more: r.length >= 100 }; }, [query]);
  const toggle = async (u: AdminUser) => {
    setErr("");
    try { await api.admin.setUserActive(u.id, !u.is_active); list.reload(); } catch (e) { setErr((e as ApiError).message); }
  };
  return (
    <section>
      <h2>Пользователи</h2>
      <p className="muted">Локальное представление доменных пользователей: они появляются после первого входа. Отключённый пользователь теряет сессию и не может войти, пока не включён снова. Пароли в системе не хранятся. Права администратора определяются членством в группе AD (LDAP_ADMIN_GROUP_DN).</p>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по имени или логину, например: иванов" style={{ maxWidth: 360 }} aria-label="Поиск пользователя" />
      {err && <div className="alert error">{err}</div>}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Имя</th><th>Логин</th><th>Почта</th><th>Последний вход</th><th>Статус</th><th /></tr></thead>
        <tbody>{list.items.map((u) => (
          <tr key={u.id}>
            <td>{u.display_name}{u.is_admin && <span className="badge"> админ</span>}</td><td>{u.sam_account_name}</td><td>{u.email ?? "—"}</td>
            <td>{u.last_login_at ? fmt(u.last_login_at) : "—"}</td>
            <td>{u.is_active ? "активен" : <span className="badge warn">отключён</span>}</td>
            <td><button className={`btn ghost ${u.is_active ? "danger" : ""}`} onClick={() => toggle(u)}>{u.is_active ? "Отключить" : "Включить"}</button></td>
          </tr>))}</tbody>
      </table></div>
      <ListFooter loading={list.loading} done={list.done} error={list.error} sentinel={list.sentinel} count={list.items.length} empty="Пользователей не найдено." />
    </section>
  );
}

export function MeetingsAdmin() {
  const [active, setActive] = useState(true);
  const [err, setErr] = useState("");
  const [forceEnd, setForceEnd] = useState<Meeting | null>(null);
  const [diag, setDiag] = useState<Meeting | null>(null);
  useEffect(() => { if (!diag) return; const prev = document.body.style.overflow; document.body.style.overflow = "hidden"; return () => { document.body.style.overflow = prev; }; }, [diag]);       // фон не прокручивается под окном
  const list = useInfinite<Meeting>(async (offset) => { const r = await api.admin.meetings(active ? true : undefined, offset); return { rows: r, more: r.length >= 50 }; }, [active]);
  // идущие встречи — короткий список: обновляем целиком; полная история не перезагружается (иначе прокрутка сбрасывалась бы)
  useEffect(() => { if (!active) return; const t = window.setInterval(list.reload, 10000); return () => window.clearInterval(t); }, [active, list.reload]);
  return (
    <section>
      <div className="row"><h2>Встречи</h2><div className="spacer" />
        <label className="check"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Только идущие</label></div>
      <p className="muted">Идущие встречи можно завершить принудительно (все участники будут отключены). Для завершённых доступны материалы, выдача доступа и удаление: отдельно только аудио или встречи целиком — с подтверждением и записью в аудит.</p>
      {(err || list.error) && <div className="alert error">{err || list.error}</div>}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Комната</th><th>Начало</th><th>Окончание</th><th>Участники</th><th>Материалы</th><th /></tr></thead>
        <tbody>{list.items.map((m) => (
          <tr key={m.id}>
            <td>{m.room_name}</td><td>{fmt(m.started_at)}</td><td>{m.ended_at ? `${fmt(m.ended_at)} (${m.end_reason ?? ""})` : <span className="badge rec">идёт</span>}</td>
            <td>{m.participants.filter((p) => p.online || m.ended_at).map((p) => p.display_name).join(", ")}</td>
            <td className="small muted">реплик {m.segments} · документов {m.protocols} · записей {m.recordings}</td>
            <td className="actions">
              <button className="btn mini diag-btn" onClick={() => setDiag(m)} title="Диагностика встречи" aria-label={`Диагностика встречи: ${m.room_name}`}>Д</button>{" "}
              {!m.ended_at && <button className="btn mini ghost danger" onClick={() => setForceEnd(m)}>Завершить</button>}{" "}
              <Link className="btn mini" to={`/history/${m.id}`}>Открыть</Link>{" "}
              {m.ended_at && <MeetingAdminActions meeting={m} onChanged={list.reload} />}
            </td>
          </tr>))}</tbody>
      </table></div>
      <ListFooter loading={list.loading} done={list.done} error={list.error} sentinel={list.sentinel} count={list.items.length} empty={active ? "Сейчас нет идущих встреч." : "Встреч пока не было."} />
      {diag && (
        <div className="diag-full" role="dialog" aria-modal="true" aria-label={`Диагностика встречи: ${diag.room_name}`} tabIndex={-1} ref={(el) => el?.focus()} onKeyDown={(e) => { if (e.key === "Escape") setDiag(null); }}>
          <div className="row diag-head"><h2>Диагностика · {diag.room_name} · {fmt(diag.started_at)}{diag.ended_at ? "" : " · идёт"}</h2><div className="spacer" />
            <button className="btn mini" onClick={() => setDiag(null)} aria-label="Закрыть диагностику">✕ Закрыть</button></div>
          <div className="diag-body"><ClientDiagAdmin meetingId={diag.id} /></div>
        </div>
      )}
      {forceEnd && (
        <ConfirmDialog title="Завершить встречу принудительно?" confirmLabel="Завершить" onClose={() => setForceEnd(null)}
          body={<p>Встреча в «{forceEnd.room_name}» будет завершена, все участники отключены. Действие записывается в журнал аудита.</p>}
          onConfirm={async () => { try { await api.admin.endMeeting(forceEnd.id); list.reload(); } catch (e) { setErr((e as ApiError).message); throw e; } }} />
      )}
    </section>
  );
}

export function AuditAdmin() {
  const [f, setF] = useState({ q: "", actor: "", action: "" });
  const [applied, setApplied] = useState(f);
  useEffect(() => { const t = window.setTimeout(() => setApplied(f), 300); return () => window.clearTimeout(t); }, [f]);
  const list = useInfinite<AuditRow>(async (offset) => { const r = await api.admin.audit(offset, applied); return { rows: r, more: r.length >= 100 }; }, [applied.q, applied.actor, applied.action]);
  return (
    <section>
      <h2>Журнал аудита</h2>
      <p className="muted">Кто, что и когда сделал: изменения настроек, удаления записей, протоколов и встреч, выдача доступа, скачивание аудио. Хранится отдельно от журнала событий и вручную не удаляется; пароли и секреты в него не попадают.
        Записи аудита также отражаются в «Журнале событий» (категория «администрирование») и попадают в его архив.</p>
      <div className="row filter-row">
        <input className="grow" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="Поиск по исполнителю, действию, объекту, IP" aria-label="Поиск" />
        <input value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })} placeholder="Кто выполнил" aria-label="Исполнитель" style={{ maxWidth: 220 }} />
        <input value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} placeholder="Действие, например room." aria-label="Действие" style={{ maxWidth: 220 }} />
      </div>
      {list.error && <div className="alert error">{list.error}</div>}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Подробности</th><th>IP</th></tr></thead>
        <tbody>{list.items.map((r) => (
          <tr key={r.id}><td>{fmt(r.at)}</td><td>{r.actor}</td><td><code>{r.action}</code></td><td>{r.target_type} <span className="muted small">{r.target_id.slice(0, 8)}</span></td>
            <td className="small">{r.details ? JSON.stringify(r.details).slice(0, 200) : ""}</td><td className="small">{r.ip ?? ""}</td></tr>))}</tbody>
      </table></div>
      <ListFooter loading={list.loading} done={list.done} error={list.error} sentinel={list.sentinel} count={list.items.length} empty="Записей по этим условиям нет." />
    </section>
  );
}

const EXPORT_LABEL: Record<string, string> = { exported: "выгружена", pending: "ожидает выгрузки", failed: "ошибка выгрузки", disabled: "хранилище выключено", local: "локально" };

export function RecordingsAdmin() {
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const list = useInfinite<RecordingRow>(async (offset) => { const r = await api.admin.recordings(offset); return { rows: r, more: r.length >= 100 }; }, []);
  const failed = list.items.filter((r) => r.export_status === "failed" || r.export_status === "pending").length;
  const retry = async () => {
    setBusy(true); setErr(""); setNote("");
    try { const r = await api.admin.retryExports(); setNote(`Выгружено: ${r.exported}, всё ещё с ошибкой: ${r.still_failed}`); list.reload(); }
    catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  return (
    <section>
      <div className="row"><h2>Записи аудио</h2><div className="spacer" />
        <button className="btn" onClick={retry} disabled={busy}>Повторить выгрузку записей{failed ? ` (${failed})` : ""}</button></div>
      <p className="muted">Файлы по участникам (WAV, 16 кГц). Срок хранения задаётся в настройках комнаты, внешнее хранилище — в разделе «Хранилище записей». Если хранилище было недоступно, запись не теряется: она остаётся локально и выгружается повторно. Скачивание доступно только администраторам и пишется в аудит.</p>
      {(err || list.error) && <div className="alert error">{err || list.error}</div>}
      {note && <div className="alert ok">{note}</div>}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Комната</th><th>Дата</th><th>Файл</th><th>Длительность</th><th>Размер</th><th>Хранилище</th><th /></tr></thead>
        <tbody>{list.items.map((r) => (
          <tr key={r.id}><td>{r.room}</td><td>{fmt(r.created_at)}</td><td>{r.path.split("/").pop()}</td><td>{r.duration_s ? `${Math.floor(r.duration_s / 60)}:${String(r.duration_s % 60).padStart(2, "0")}` : "—"}</td>
            <td>{bytes(r.size_bytes)}</td>
            <td>{r.export_status === "failed" ? <span className="badge warn" title={r.export_error ?? ""}>{EXPORT_LABEL.failed}</span> : EXPORT_LABEL[r.export_status ?? ""] ?? r.export_status ?? "—"}
              {r.export_error && <div className="small muted">{r.export_error}</div>}</td>
            <td>{r.file_state === "missing" ? <span className="badge warn" title="Файл удалён из хранилища вне приложения (обнаружено при сверке). Ссылка на скачивание не выдаётся.">файл удалён</span> : <a href={`/api/v1/meetings/${r.meeting_id}/recordings/${r.id}`} download>Скачать</a>}</td></tr>))}</tbody>
      </table></div>
      <ListFooter loading={list.loading} done={list.done} error={list.error} sentinel={list.sentinel} count={list.items.length} empty="Записей пока нет." />
    </section>
  );
}
