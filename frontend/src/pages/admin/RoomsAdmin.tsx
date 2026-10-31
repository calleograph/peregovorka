import { useCallback, useEffect, useState } from "react";
import { copyText } from "../../util";
import { useContextMenu } from "../../components/ContextMenu";
import { api, type ApiError, type RoomAdmin } from "../../api";
import { entryLabel } from "../../components/AclPicker";
import RoomIcon from "../../components/RoomIcon";
import RoomEditor from "../../components/RoomEditor";
import { useFocusOnMount } from "../../useInitialFocus";
import { ConfirmDialog } from "../../components/Dialogs";

const peopleText = (r: RoomAdmin) => {
  const n = r.active_people ?? 0, c = r.active_connections;
  return `${n} ${n % 10 === 1 && n % 100 !== 11 ? "участник" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "участника" : "участников"}${c != null && c !== n ? `, подключений: ${c}` : ""}`;
};

/** «Комнаты и доступ»: список комнат и единый редактор (components/RoomEditor.tsx) — те же вкладки при создании и изменении, что и в настройках комнаты. */
export default function RoomsAdmin() {
  const focusOnMount = useFocusOnMount();
  const { onContextMenu, node: ctxNode } = useContextMenu();
  const [rooms, setRooms] = useState<RoomAdmin[]>([]);
  const [editing, setEditing] = useState<{ id?: string; key: number } | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [showClosed, setShowClosed] = useState(false);
  const [ending, setEnding] = useState<RoomAdmin | null>(null);

  const load = useCallback(() => api.admin.rooms(showClosed).then(setRooms).catch((e) => setError(e.message)), [showClosed]);
  useEffect(() => { void load(); }, [load]);

  const open = (id?: string) => { setEditing({ id, key: Date.now() }); setError(""); setNote(""); };
  const remove = async (r: RoomAdmin) => {
    if (!window.confirm(`Удалить комнату «${r.name}» вместе с историей встреч?`)) return;
    try { await api.admin.deleteRoom(r.id); await load(); } catch (err) { setError((err as ApiError).message); }
  };
  const current = editing?.id ? rooms.find((r) => r.id === editing.id) : undefined;

  return (
    <section>
      <div className="row"><h2>Комнаты и доступ</h2><div className="spacer" />
        <label className="check small" style={{ margin: 0 }}><input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> показывать закрытые временные</label>
        {!editing && <button className="btn primary" onClick={() => open()}>＋ Создать комнату</button>}</div>
      {note && <div className="alert ok">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}

      {editing && (
        <div ref={focusOnMount} className="card">
          <h3 style={{ margin: "0 0 4px" }}>{editing.id ? `Изменение комнаты «${current?.name ?? ""}»` : "Новая комната"}</h3>
          <RoomEditor key={editing.key} roomId={editing.id} admin adminRoom={current} onClose={() => { setEditing(null); void load(); }}
                      onSaved={() => void load()} onCreated={(id) => { setNote("Комната создана. Можно продолжить настройку: трансляция, гостевая ссылка."); void load().then(() => setEditing({ id, key: Date.now() })); }} />
        </div>
      )}

      {ctxNode}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Название</th><th>Состояние</th><th>Доступ</th><th>Руководители</th><th>Опции</th><th /></tr></thead>
        <tbody>
          {rooms.map((r) => (
            <tr key={r.id} onContextMenu={onContextMenu(() => [
              { id: "open", label: "Открыть комнату", icon: "arrowR", onSelect: () => window.open(`/rooms/${r.slug}`, "_blank", "noopener") },
              { id: "edit", label: "Настроить", icon: "gear", onSelect: () => open(r.id) },
              { id: "url", label: "Скопировать URL", icon: "copy", onSelect: () => void copyText(`${window.location.origin}/rooms/${r.slug}`) },
              { id: "end", label: "Завершить встречу для всех", icon: "close", danger: true, hidden: !r.active_meeting_id, onSelect: () => setEnding(r) },
              { id: "del", label: "Удалить", icon: "close", danger: true, confirm: `Удалить переговорку «${r.name}»?`, onSelect: () => remove(r) },
            ])}>
              <td><span className="h-room-link"><RoomIcon name={r.name} icon={r.icon} size={26} />{r.name}</span>{r.lifetime === "temporary" && <span className="badge"> временная</span>}<div className="muted small"><code>{r.slug}</code>{r.created_by_name ? ` · создал(а): ${r.created_by_name}` : ""}</div></td>
              <td>{r.lifecycle === "closed" ? <span className="badge">закрыта {r.closed_at ? new Date(r.closed_at).toLocaleString("ru-RU") : ""}</span> : r.lifecycle === "grace_period" ? <span className="badge warn">ждёт возврата</span> : r.is_enabled ? "включена" : <span className="badge warn">отключена</span>}{r.active_meeting_id && <><span className="badge rec"> идёт встреча · {peopleText(r)}</span>
                  <div><button className="btn mini ghost danger" onClick={() => setEnding(r)}>Завершить встречу</button></div></>}
                <div className="muted small">история: {r.history_access === "participants" ? "участникам" : "админам"}</div></td>
              <td className="small">{r.acl.length ? `${r.acl.length} запис.` : "только админы"}</td>
              <td className="small">{r.moderators?.length ? r.moderators.map(entryLabel).join(", ") : <span className="muted">—</span>}</td>
              <td className="small">{[r.room_type === "presentation" && "презентация", r.room_type === "broadcast" && "трансляция", r.has_password && "пароль", r.auto_record ? "автозапись" : r.record_audio && "запись по кнопке", r.camera_allowed && "камера", r.screen_share_allowed && "экран", r.mute_on_join && "вход без звука", r.guest_access_enabled && "гости",
                r.anonymize_mode === "off" && "без обезличивания", r.anonymize_mode === "on" && "обезличивание всегда", (r.llm_profile_id || r.anonymizer_profile_id) && "свой API"].filter(Boolean).join(", ")}</td>
              <td className="actions"><button className="btn ghost" onClick={() => open(r.id)}>Изменить</button><button className="btn ghost danger" onClick={() => remove(r)}>Удалить</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {ending?.active_meeting_id && (
        <ConfirmDialog title={`Завершить встречу в «${ending.name}» для всех?`} confirmLabel="Завершить встречу для всех" onClose={() => setEnding(null)}
          body={<><p>Сейчас во встрече: {peopleText(ending)}.</p>
            <p>Все участники будут отключены, идущие запись и трансляция — завершены; записи, стенограмма и протокол сохранятся. Комната останется доступной для следующих встреч
              {ending.lifetime === "temporary" ? " (временная комната закроется, как после обычного завершения)" : ""}. Действие записывается в журнал аудита.</p></>}
          onConfirm={async () => { await api.admin.endMeeting(ending.active_meeting_id!); setNote(`Встреча в «${ending.name}» завершена.`); await load(); }} />
      )}
    </section>
  );
}
