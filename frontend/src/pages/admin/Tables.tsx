import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, auditParams, type AdminUser, type ApiError, type AuditFilter, type AuditRow, type AuditVerify, type Meeting, type RecordingRow, type SessionInfo } from "../../api";
import MeetingAdminActions from "../../components/MeetingAdminActions";
import { ConfirmDialog } from "../../components/Dialogs";
import ClientDiagAdmin from "./ClientDiagAdmin";
import { ListFooter, useInfinite } from "../../useInfinite";
import { bytes, fmt } from "../../util";
import InfoTip from "../../components/InfoTip";

export function UsersAdmin() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { const t = window.setTimeout(() => setQuery(q), 250); return () => window.clearTimeout(t); }, [q]);
  const list = useInfinite<AdminUser>(async (offset) => { const r = await api.admin.users(query, offset); return { rows: r, more: r.length >= 100 }; }, [query]);
  const [reset, setReset] = useState<AdminUser | null>(null);
  const [rpw, setRpw] = useState("");
  const doReset = async () => {
    if (!reset) return;
    try { await api.admin.resetTotp(reset.id, rpw); setReset(null); setRpw(""); list.reload(); } catch (e) { setErr((e as ApiError).message); }
  };
  const [sessOf, setSessOf] = useState<{ user: AdminUser; items: SessionInfo[] } | null>(null);
  const showSessions = async (u: AdminUser) => { setErr(""); try { setSessOf({ user: u, items: (await api.admin.userSessions(u.id)).items }); } catch (e) { setErr((e as ApiError).message); } };
  const endSess = async (u: AdminUser, pub?: string) => { try { await api.admin.endUserSessions(u.id, pub); await showSessions(u); } catch (e) { setErr((e as ApiError).message); } };
  const toggle = async (u: AdminUser) => {
    setErr("");
    try { await api.admin.setUserActive(u.id, !u.is_active); list.reload(); } catch (e) { setErr((e as ApiError).message); }
  };
  return (
    <section>
      <h2>Пользователи</h2>
      <p className="muted small">Локальное представление доменных пользователей: они появляются после первого входа.<InfoTip>Отключённый пользователь теряет сессию и не может войти, пока не включён снова. Пароли в системе не хранятся. Права администратора определяются членством в группе AD (LDAP_ADMIN_GROUP_DN).</InfoTip></p>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по имени или логину, например: иванов" style={{ maxWidth: 360 }} aria-label="Поиск пользователя" />
      {err && <div className="alert error">{err}</div>}
      {sessOf && (
        <div className="card" role="dialog" aria-label="Сеансы пользователя">
          <div className="row"><h3 style={{ margin: 0 }}>Сеансы: {sessOf.user.display_name}</h3><div className="spacer" />
            <button className="btn mini danger" disabled={!sessOf.items.length} onClick={() => void endSess(sessOf.user)}>Завершить все</button>
            <button className="btn mini" onClick={() => setSessOf(null)}>Закрыть</button></div>
          {!sessOf.items.length ? <p className="muted">Активных сеансов нет.</p> : (
            <ul className="sess-list">{sessOf.items.map((s) => (
              <li key={s.id}><span><b>{s.device}</b><small className="muted"> · {s.ip || "адрес не определён"} · активность {s.last_seen ? new Date(s.last_seen * 1000).toLocaleString("ru-RU") : "—"}</small></span>
                <button className="btn mini" onClick={() => void endSess(sessOf.user, s.id)}>Завершить</button></li>))}</ul>)}
        </div>)}
      {reset && (
        <form className="card form" onSubmit={(e) => { e.preventDefault(); void doReset(); }}>
          <p>Сбросить двухфакторную защиту пользователя <b>{reset.display_name}</b>? Резервные коды тоже будут удалены; он настроит защиту заново при следующем входе (если политика её требует). Действие записывается в аудит.</p>
          <label>Ваш пароль администратора<input type="password" value={rpw} onChange={(e) => setRpw(e.target.value)} autoComplete="current-password" autoFocus required /></label>
          <div className="row"><button className="btn danger" disabled={!rpw}>Сбросить</button><button type="button" className="btn" onClick={() => { setReset(null); setRpw(""); }}>Отмена</button></div>
        </form>)}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Имя</th><th>Логин</th><th>Почта</th><th>Последний вход</th><th>2FA</th><th>Статус</th><th /></tr></thead>
        <tbody>{list.items.map((u) => (
          <tr key={u.id}>
            <td>{u.display_name}{u.is_admin && <span className="badge"> админ</span>}</td><td>{u.sam_account_name}</td><td>{u.email ?? "—"}</td>
            <td>{u.last_login_at ? fmt(u.last_login_at) : "—"}</td>
            <td>{u.totp_enabled ? <>включена <button className="btn mini ghost danger" onClick={() => setReset(u)} title="Пользователь потерял устройство: сбросить, чтобы он настроил 2FA заново">сбросить</button></> : <span className="muted">нет</span>}</td>
            <td>{u.is_active ? "активен" : <span className="badge warn">отключён</span>}</td>
            <td><button className="btn ghost" onClick={() => void showSessions(u)} title="Активные устройства пользователя">Сеансы</button> <button className={`btn ghost ${u.is_active ? "danger" : ""}`} onClick={() => toggle(u)}>{u.is_active ? "Отключить" : "Включить"}</button></td>
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
      <p className="muted small">Идущие встречи можно завершить принудительно (все участники будут отключены).<InfoTip>Для завершённых доступны материалы, выдача доступа и удаление: отдельно только аудио или встречи целиком — с подтверждением и записью в аудит.</InfoTip></p>
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
  const [f, setF] = useState<Required<AuditFilter>>({ q: "", actor: "", action: "", result: "", actor_type: "", object_type: "", ip: "", since: "", until: "" });
  const [applied, setApplied] = useState(f);
  const [ver, setVer] = useState<AuditVerify | null>(null);
  const [verErr, setVerErr] = useState("");
  const verify = async () => { setVerErr(""); try { setVer(await api.admin.auditVerify()); } catch (e) { setVerErr((e as ApiError).message); } };
  const upd = (k: keyof AuditFilter, v: string) => setF({ ...f, [k]: v });
  useEffect(() => { const t = window.setTimeout(() => setApplied(f), 300); return () => window.clearTimeout(t); }, [f]);
  const list = useInfinite<AuditRow>(async (offset) => { const r = await api.admin.audit(offset, applied); return { rows: r, more: r.length >= 100 }; }, [auditParams(applied)]);
  return (
    <section>
      <h2>Журнал аудита</h2>
      <p className="muted small">Кто, что и когда сделал: изменения настроек, удаления записей, протоколов и встреч, выдача доступа, скачивание аудио.<InfoTip>Хранится отдельно от журнала событий и вручную не удаляется; пароли и секреты в него не попадают. Записи аудита также отражаются в «Журнале событий» (категория «администрирование») и попадают в его архив. Время хранится в UTC.</InfoTip></p>
      <div className="row filter-row">
        <input className="grow" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="Поиск по исполнителю, действию, объекту, IP" aria-label="Поиск" />
        <input value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })} placeholder="Кто выполнил" aria-label="Исполнитель" style={{ maxWidth: 220 }} />
        <input value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} placeholder="Действие, например room." aria-label="Действие" style={{ maxWidth: 220 }} />
      </div>
      <div className="row filter-row">
        <select value={f.result} onChange={(e) => upd("result", e.target.value)} aria-label="Результат"><option value="">Любой результат</option><option value="success">успех</option><option value="denied">отказ</option><option value="failure">ошибка</option></select>
        <select value={f.actor_type} onChange={(e) => upd("actor_type", e.target.value)} aria-label="Тип исполнителя"><option value="">Любой исполнитель</option><option value="user">пользователь</option><option value="guest">гость</option><option value="system">система</option><option value="service">сервис</option></select>
        <input value={f.object_type} onChange={(e) => upd("object_type", e.target.value)} placeholder="Тип объекта" aria-label="Тип объекта" style={{ maxWidth: 160 }} />
        <input value={f.ip} onChange={(e) => upd("ip", e.target.value)} placeholder="IP" aria-label="IP" style={{ maxWidth: 160 }} />
        <label className="small">с <input type="date" value={f.since} onChange={(e) => upd("since", e.target.value)} aria-label="С даты" /></label>
        <label className="small">по <input type="date" value={f.until} onChange={(e) => upd("until", e.target.value)} aria-label="По дату" /></label>
        <div className="spacer" />
        <a className="btn" href={api.admin.auditExportUrl("csv", applied)} download title="Выгрузка по текущим фильтрам, до 100 000 записей">⬇ CSV</a>
        <a className="btn" href={api.admin.auditExportUrl("json", applied)} download>⬇ JSON</a>
        <button className="btn" onClick={verify} title="Проверка подписей записей и непрерывности нумерации">Проверить целостность</button>
      </div>
      {verErr && <div className="alert error">{verErr}</div>}
      {ver && <div className={`alert ${ver.signed && ver.intact ? "ok" : "error"}`}>
        {!ver.signed ? ver.note : ver.intact ? `Целостность подтверждена: проверено записей ${ver.checked}${ver.unsigned ? `, без подписи (старые) ${ver.unsigned}` : ""}.`
          : `Нарушена целостность: изменённых записей ${ver.bad.length}, пропусков в нумерации ${ver.gaps.length}.`} <span className="muted small">{ver.signed ? ver.note : ""}</span></div>}
      {list.error && <div className="alert error">{list.error}</div>}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Подробности</th><th>Результат</th><th>IP</th></tr></thead>
        <tbody>{list.items.map((r) => (
          <tr key={r.id}><td>{fmt(r.at)}</td><td>{r.actor}{r.actor_type && r.actor_type !== "user" ? <span className="muted small"> ({r.actor_type})</span> : null}</td><td><code>{r.action}</code></td><td>{r.target_type} <span className="muted small">{r.target_id.slice(0, 8)}</span></td>
            <td className="small">{r.details ? JSON.stringify(r.details).slice(0, 200) : ""}</td><td>{r.result && r.result !== "success" ? <span className="badge warn">{r.result === "denied" ? "отказ" : "ошибка"}</span> : "успех"}</td><td className="small" title={r.user_agent ?? ""}>{r.ip ?? ""}</td></tr>))}</tbody>
      </table></div>
      <ListFooter loading={list.loading} done={list.done} error={list.error} sentinel={list.sentinel} count={list.items.length} empty="Записей по этим условиям нет." />
    </section>
  );
}

const EXPORT_LABEL: Record<string, string> = { exported: "выгружена", pending: "ожидает выгрузки", failed: "ошибка выгрузки", disabled: "хранилище выключено", local: "локально" };

const KIND_LABEL: Record<string, string> = { mix_video: "видеозапись", mix_audio: "общая аудиозапись", participant: "участник" };

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
      <div className="row"><h2>Записи встреч</h2><div className="spacer" />
        <button className="btn" onClick={retry} disabled={busy}>Повторить выгрузку записей{failed ? ` (${failed})` : ""}</button></div>
      <p className="muted small">Видеозаписи (MP4), общие аудиозаписи (M4A) и файлы участников (WAV, 16 кГц).<InfoTip>Срок хранения аудиозаписей задаётся в настройках комнаты, видеозаписей — в разделе «Видеозапись»; внешнее хранилище — в «Хранилище аудиозаписей» и «Видеозапись». Если хранилище было недоступно, запись не теряется: она остаётся локально и выгружается повторно. Скачивание доступно только администраторам и пишется в аудит.</InfoTip></p>
      {(err || list.error) && <div className="alert error">{err || list.error}</div>}
      {note && <div className="alert ok">{note}</div>}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Комната</th><th>Дата</th><th>Файл</th><th>Длительность</th><th>Размер</th><th>Хранилище</th><th /></tr></thead>
        <tbody>{list.items.map((r) => (
          <tr key={r.id}><td>{r.room}</td><td>{fmt(r.created_at)}</td><td>{r.path.split("/").pop()}<div className="muted small">{KIND_LABEL[r.kind ?? ""] ?? "запись"}</div></td><td>{r.duration_s ? `${Math.floor(r.duration_s / 60)}:${String(r.duration_s % 60).padStart(2, "0")}` : "—"}</td>
            <td>{bytes(r.size_bytes)}</td>
            <td>{r.export_status === "failed" ? <span className="badge warn" title={r.export_error ?? ""}>{EXPORT_LABEL.failed}</span> : EXPORT_LABEL[r.export_status ?? ""] ?? r.export_status ?? "—"}
              {r.export_error && <div className="small muted">{r.export_error}</div>}</td>
            <td>{r.file_state === "missing" ? <span className="badge warn" title="Файл удалён из хранилища вне приложения (обнаружено при сверке). Ссылка на скачивание не выдаётся.">файл удалён</span> : <a href={`/api/v1/meetings/${r.meeting_id}/recordings/${r.id}`} download>Скачать</a>}</td></tr>))}</tbody>
      </table></div>
      <ListFooter loading={list.loading} done={list.done} error={list.error} sentinel={list.sentinel} count={list.items.length} empty="Записей пока нет." />
    </section>
  );
}
