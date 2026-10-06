import { useCallback, useEffect, useState } from "react";
import { api, type AdminUser, type ApiError, type Grant, type Meeting } from "../api";
import { ConfirmDialog, Modal } from "./Dialogs";
import Menu from "./Menu";

type M = Pick<Meeting, "id" | "room_name" | "recordings" | "ended_at">;

/** Явный доступ к завершённой встрече для выбранных пользователей (помимо участников на странице и политики комнаты). */
function GrantsDialog({ meetingId, onClose }: { meetingId: string; onClose: () => void }) {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<AdminUser[]>([]);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.grants(meetingId).then(setGrants).catch((e) => setErr((e as ApiError).message)), [meetingId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = window.setTimeout(() => api.admin.users(q).then(setHits).catch(() => setHits([])), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const add = async (u: AdminUser) => { setErr(""); try { await api.admin.addGrant(meetingId, u.id); setQ(""); await load(); } catch (e) { setErr((e as ApiError).message); } };
  const remove = async (g: Grant) => { setErr(""); try { await api.admin.removeGrant(meetingId, g.user_id); await load(); } catch (e) { setErr((e as ApiError).message); } };
  return (
    <Modal title="Доступ к встрече" onClose={onClose}>
      <p className="muted">По умолчанию завершённую встречу видят только администраторы и те участники, которые остаются на её странице (а также участники, если так задано в настройках комнаты). Здесь можно выдать доступ конкретным пользователям — он действует, пока вы его не уберёте.</p>
      {err && <div className="alert error" role="alert">{err}</div>}
      <table className="table compact"><tbody>
        {grants.length === 0 && <tr><td className="muted">Явных разрешений нет.</td></tr>}
        {grants.map((g) => <tr key={g.user_id}><td>{g.display_name} <span className="muted small">({g.sam_account_name}){g.granted_by ? `, выдал: ${g.granted_by}` : ""}</span></td>
          <td className="actions"><button className="btn mini ghost danger" onClick={() => remove(g)}>Убрать</button></td></tr>)}
      </tbody></table>
      <label>Добавить пользователя
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Имя или логин (от 2 символов; пользователь должен был хотя бы раз войти)" />
        <span className="example">Пример: <code>ivanov</code> или <code>Иванов</code></span>
      </label>
      {hits.map((u) => <div key={u.id} className="hit"><span>{u.display_name} <span className="muted small">{u.sam_account_name}</span></span><button className="btn mini" onClick={() => add(u)}>Добавить</button></div>)}
    </Modal>
  );
}

/** Админские действия над встречей: доступ, удаление аудиозаписи (только звук), удаление встречи целиком. Всё подтверждается и пишется в аудит. */
export default function MeetingAdminActions({ meeting, onChanged, onDeleted }: { meeting: M; onChanged: () => void; onDeleted?: () => void }) {
  const [dlg, setDlg] = useState<"grants" | "audio" | "meeting" | null>(null);
  const close = () => setDlg(null);
  return (
    <>
      <Menu label="Администрирование" title="Действия администратора">
        <button onClick={() => setDlg("grants")}>Доступ к встрече…</button>
        {meeting.recordings > 0 && <button onClick={() => setDlg("audio")}>Удалить запись (только аудио)…</button>}
        <button onClick={() => setDlg("meeting")} style={{ color: "var(--danger-text)" }}>Удалить встречу со всеми материалами…</button>
      </Menu>
      {dlg === "grants" && <GrantsDialog meetingId={meeting.id} onClose={close} />}
      {dlg === "audio" && (
        <ConfirmDialog title="Удалить запись?" confirmLabel="Удалить запись" onClose={close}
          body={<><p>Будут удалены <b>только аудиозаписи</b> встречи «{meeting.room_name}» ({meeting.recordings} шт.) — локальные файлы и копии во внешнем хранилище записей.</p>
            <p>Стенограмма и протоколы останутся. Действие необратимо и записывается в журнал аудита (кто, что, когда).</p></>}
          onConfirm={async () => { await api.deleteRecordings(meeting.id); onChanged(); }} />
      )}
      {dlg === "meeting" && (
        <ConfirmDialog title="Удалить встречу со всеми материалами?" confirmLabel="Удалить встречу" typed="УДАЛИТЬ" onClose={close}
          body={<><p>Встреча «{meeting.room_name}» будет удалена <b>полностью</b>: стенограмма, протоколы и резюме, аудиозаписи, список участников и выданные доступы.</p>
            <p>Копии, уже выгруженные во внешнее хранилище протоколов, система не удаляет. Действие необратимо; в журнале аудита останется запись о том, кто и когда её выполнил.</p></>}
          onConfirm={async () => { await api.deleteMeeting(meeting.id); (onDeleted ?? onChanged)(); }} />
      )}
    </>
  );
}
