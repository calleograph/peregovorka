import { useEffect, useState, type FormEvent } from "react";
import { api, type AclEntry, type ApiError, type LlmChoice, type MailDeliverySpec, type RoomManage, type RoomSip, type RoomType } from "../api";
import { copyText } from "../util";
import AclPicker from "./AclPicker";
import { Modal } from "./Dialogs";
import DeliveryEditor, { emptySpec } from "./DeliveryEditor";
import LlmChoiceEditor from "./LlmChoiceEditor";
import TelephonyEditor from "./TelephonyEditor";
import { choiceLabel, emptyChoice } from "../phone";

/** Разделы сгруппированы по смыслу: что за комната, как проходит встреча, кто допущен, что остаётся после встречи, какая модель и телефония. */
type TabId = "main" | "mode" | "access" | "materials" | "model" | "phone";
const TABS: [TabId, string][] = [["main", "Основное"], ["mode", "Встреча и запись"], ["access", "Доступ и гости"], ["materials", "Материалы после встречи"], ["model", "Языковая модель"], ["phone", "Телефония"]];
const emptySip = (): RoomSip => ({ mode: "off", profile_id: null, extension: null, allow_inbound: false, allow_outbound: false, contacts: [] });
const days = (d: number | null | undefined) => (d == null ? "бессрочно" : `${d} дн.`);

interface Form {
  name: string; description: string; max_participants: number; password: string; clearPassword: boolean; welcome_message: string; mute_on_join: boolean;
  room_type: RoomType; record_audio: boolean; auto_record: boolean; camera_allowed: boolean; screen_share_allowed: boolean; board_allowed: boolean;
  guest_access_enabled: boolean; acl: AclEntry[]; moderators: AclEntry[]; mail_delivery: MailDeliverySpec;
  protocol_instructions: string; llm: LlmChoice; llm_summary: LlmChoice; sip: RoomSip;
}

const toForm = (r: RoomManage): Form => ({
  name: r.name, description: r.description ?? "", max_participants: r.max_participants, password: "", clearPassword: false,
  welcome_message: r.welcome_message ?? "", mute_on_join: r.mute_on_join, room_type: r.room_type, record_audio: r.record_audio, auto_record: r.auto_record,
  camera_allowed: r.camera_allowed, screen_share_allowed: r.screen_share_allowed, board_allowed: r.board_allowed, guest_access_enabled: r.guest_access_enabled,
  acl: r.acl, moderators: r.moderators, mail_delivery: r.mail_delivery && (r.mail_delivery.enabled || r.mail_delivery.materials.length) ? r.mail_delivery : emptySpec(),   // для нового — разумные значения по умолчанию
  protocol_instructions: r.protocol_instructions ?? "", llm: r.llm ?? emptyChoice(), llm_summary: r.llm_summary ?? emptyChoice(), sip: r.sip ?? emptySip(),
});

/**
 * «Настройки комнаты» для руководителя: всё, чем он управляет сам — название, режим (обычная / презентационная), запись, права участников,
 * доступ и руководители, гости. Системные настройки (языковая модель, хранилища, сроки хранения, каталог) здесь намеренно отсутствуют:
 * они остаются у администратора сервера.
 */
export default function RoomManageDialog({ roomId, onClose, onSaved }: { roomId: string; onClose: () => void; onSaved?: (r: RoomManage) => void }) {
  const [room, setRoom] = useState<RoomManage | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [tab, setTab] = useState<TabId>("main");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api.manage.get(roomId).then((r) => { if (alive) { setRoom(r); setForm(toForm(r)); } })
      .catch((e) => { if (alive) setError((e as ApiError).message || "Не удалось загрузить настройки"); });
    return () => { alive = false; };
  }, [roomId]);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form || busy) return;
    if (!form.name.trim()) { setTab("main"); setError("Укажите название комнаты."); return; }
    if (form.sip.mode !== "off" && form.sip.allow_inbound && !form.sip.extension) { setTab("phone"); setError("Для входящих звонков укажите внутренний номер комнаты."); return; }
    setBusy(true); setError(""); setNote("");
    try {
      const r = await api.manage.patch(roomId, {
        name: form.name.trim(), description: form.description.trim() || null, max_participants: form.max_participants,
        welcome_message: form.welcome_message.trim() || null, mute_on_join: form.mute_on_join, room_type: form.room_type,
        record_audio: form.record_audio || form.auto_record, auto_record: form.auto_record, camera_allowed: form.camera_allowed,
        screen_share_allowed: form.screen_share_allowed, board_allowed: form.board_allowed, guest_access_enabled: form.guest_access_enabled,
        acl: form.acl, moderators: form.moderators, mail_delivery: form.mail_delivery,
        protocol_instructions: form.protocol_instructions.trim() || null, llm: form.llm, llm_summary: form.llm_summary, sip: form.sip,
        ...(form.clearPassword ? { password: "" } : form.password ? { password: form.password } : {}),
      });
      setRoom(r); setForm(toForm(r)); onSaved?.(r);
      setNote(r.needs_rejoin ? "Сохранено. Режим комнаты, камера и показ экрана для тех, кто уже на встрече, изменятся при следующем входе; слово можно давать и забирать сразу." : "Сохранено.");
    } catch (err) { setError((err as ApiError).message || "Не удалось сохранить"); }
    setBusy(false);
  };

  const guestAct = async (action: "rotate" | "revoke") => {
    const q = action === "rotate"
      ? "Выпустить новую гостевую ссылку? Прежняя перестанет работать, гости, которые сейчас на встрече, будут отключены."
      : "Отозвать гостевую ссылку? Гостевой доступ будет выключен, гости, которые сейчас на встрече, будут отключены.";
    if (!window.confirm(q)) return;
    setBusy(true); setError(""); setNote("");
    try {
      const r = await api.manage.guestLink(roomId, action);
      setRoom(r); set("guest_access_enabled", r.guest_access_enabled);
      setNote(action === "rotate" ? "Новая ссылка выпущена." : "Ссылка отозвана.");
    } catch (err) { setError((err as ApiError).message); }
    setBusy(false);
  };

  const url = room?.guest_token ? `${window.location.origin}/guest/${room.guest_token}` : "";

  return (
    <Modal title={room ? `Настройки комнаты «${room.name}»` : "Настройки комнаты"} onClose={onClose} wide>
      {!form && !error && <p className="muted">Загрузка…</p>}
      {error && !form && <div className="alert error" role="alert">{error}</div>}
      {form && room && (
        <form className="form room-form" onSubmit={save} noValidate>
          {room.lifetime === "temporary" && <p className="muted small" style={{ marginTop: 0 }}>Временная переговорка: показаны только основные настройки. Кого пригласить — на вкладке «Доступ и гости»; языковая модель, телефония и рассылка материалов берутся из системных настроек.</p>}
          <div className="tabs" role="tablist" aria-label="Разделы настроек комнаты">
            {TABS.filter(([id]) => room.lifetime !== "temporary" || !["model", "phone", "materials"].includes(id)).map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>)}
          </div>

          {tab === "main" && (
            <div role="tabpanel">
              <label>Название<input value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={200} /></label>
              <label>Описание<input value={form.description} onChange={(e) => set("description", e.target.value)} maxLength={2000} placeholder="Еженедельная планёрка отдела" /></label>
              <div className="cols">
                <label>Максимум участников<input type="number" min={1} max={200} value={form.max_participants} onChange={(e) => set("max_participants", Number(e.target.value))} /></label>
                <label>Пароль комнаты {room.has_password && <span className="muted small">(пусто — не менять)</span>}
                  <input type="password" value={form.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" disabled={form.clearPassword} /></label>
              </div>
              {room.has_password && <label className="check"><input type="checkbox" checked={form.clearPassword} onChange={(e) => set("clearPassword", e.target.checked)} /> Убрать пароль</label>}
              <label className="check"><input type="checkbox" checked={form.mute_on_join} onChange={(e) => set("mute_on_join", e.target.checked)} />
                <span className="check-body">Микрофон при входе выключен<span className="help">Участники заходят без звука и включают микрофон сами.</span></span></label>
              <label>Приветствие при входе <span className="muted small">(необязательно)</span>
                <textarea rows={2} value={form.welcome_message} onChange={(e) => set("welcome_message", e.target.value)} maxLength={2000} /></label>
            </div>
          )}

          {tab === "mode" && (
            <div role="tabpanel">
              <fieldset className="group"><legend>Тип комнаты</legend>
                <label className="check"><input type="radio" name="rt" checked={form.room_type === "regular"} onChange={() => set("room_type", "regular")} />
                  <span className="check-body">Обычная<span className="help">Все участники могут говорить, включать камеру и показывать экран (если это разрешено ниже).</span></span></label>
                <label className="check"><input type="radio" name="rt" checked={form.room_type === "presentation"} onChange={() => set("room_type", "presentation")} />
                  <span className="check-body">Презентационная<span className="help">Участники входят слушателями: микрофон, камера, показ экрана и доска у них закрыты. Говорят руководители и те, кому руководитель «дал слово» на встрече — временно, до конца встречи.</span></span></label>
              </fieldset>
              <fieldset className="group"><legend>Запись и транскрибация</legend>
                <p className="help" style={{ marginTop: 0 }}>Транскрибация включена всегда. Во время встречи руководитель может приостановить и возобновить её кнопкой «Остановить транскрибацию» — звонок и запись звука при этом не затрагиваются.</p>
                <label className="check"><input type="checkbox" checked={form.record_audio || form.auto_record} disabled={form.auto_record} onChange={(e) => set("record_audio", e.target.checked)} />
                  <span className="check-body">Разрешить запись аудио<span className="help">Руководитель сможет включать и выключать запись кнопкой во время встречи.</span></span></label>
                <label className="check"><input type="checkbox" checked={form.auto_record} onChange={(e) => set("auto_record", e.target.checked)} />
                  <span className="check-body">Начинать запись автоматически<span className="help">Запись начинается вместе со встречей. Если выключено — руководитель включает её вручную.</span></span></label>
              </fieldset>
              <fieldset className="group"><legend>Что доступно участникам</legend>
                <div className="checks">
                  <label className="check"><input type="checkbox" checked={form.camera_allowed} onChange={(e) => set("camera_allowed", e.target.checked)} /> Камера</label>
                  <label className="check"><input type="checkbox" checked={form.screen_share_allowed} onChange={(e) => set("screen_share_allowed", e.target.checked)} /> Показ экрана</label>
                  <label className="check"><input type="checkbox" checked={form.board_allowed} onChange={(e) => set("board_allowed", e.target.checked)} /> Общая доска</label>
                </div>
                <span className="help">Руководители комнаты могут всё независимо от этих галочек. В презентационной комнате права участников действуют, пока у них есть слово.</span>
              </fieldset>
            </div>
          )}

          {tab === "access" && (
            <div role="tabpanel">
              <AclPicker acl={form.acl} moderators={form.moderators} onChange={(n) => setForm((f) => (f ? { ...f, ...n } : f))}
                         search={(kind, q) => api.manage.search(roomId, kind, q)}
                         leaderEmpty="Руководителей нет — комнатой смогут управлять только администраторы сервера." />
              <p className="help">Нельзя оставить комнату совсем без руководителей: тогда управлять ею будет некому, кроме администратора сервера.</p>
              <fieldset className="group"><legend>Гостевой доступ</legend>
                <label className="check"><input type="checkbox" checked={form.guest_access_enabled} onChange={(e) => set("guest_access_enabled", e.target.checked)} />
                  <span className="check-body">Разрешить вход по гостевой ссылке<span className="help">Гость вводит имя и попадает в идущую встречу как «Имя (гость)»: без истории, стенограммы и показа экрана. Выключение отключает гостей, ссылка сохраняется.</span></span></label>
                {room.guest_access_enabled && url ? (
                  <div className="guest-link">
                    <div className="row tight"><input readOnly value={url} onFocus={(e) => e.currentTarget.select()} aria-label="Гостевая ссылка" />
                      <button type="button" className="btn mini" onClick={async () => setNote((await copyText(url)) ? "Ссылка скопирована." : "Не удалось скопировать — выделите ссылку вручную.")}>Копировать</button></div>
                    <div className="row tight">
                      <button type="button" className="btn mini" disabled={busy} onClick={() => void guestAct("rotate")}>Выпустить новую ссылку</button>
                      <button type="button" className="btn mini danger" disabled={busy} onClick={() => void guestAct("revoke")}>Отозвать ссылку</button>
                    </div>
                  </div>
                ) : <p className="muted small">{room.guest_access_enabled ? "" : "Гостевой доступ выключен. Включите и сохраните — ссылка появится здесь."}</p>}
              </fieldset>
            </div>
          )}

          {tab === "materials" && (
            <div role="tabpanel">
              <fieldset className="group"><legend>Хранение</legend>
                <p className="help" style={{ marginTop: 0 }}>Куда складываются стенограмма, протокол, переписка и схема, и как долго — задаёт администратор сервера; здесь это только для сведения.</p>
                <table className="table compact"><tbody>
                  <tr><td>Текст встреч хранится</td><td>{days(room.retention?.text_days)}</td></tr>
                  <tr><td>Записи аудио хранятся</td><td>{room.record_audio || form.auto_record ? days(room.retention?.audio_days) : "запись не ведётся"}</td></tr>
                  <tr><td>Кто видит завершённую встречу</td><td>{room.retention?.history_access === "participants" ? "участники встречи" : "руководители и администраторы (и участники, пока открыта страница встречи)"}</td></tr>
                  <tr><td>Обезличивание перед внешней моделью</td><td>{room.retention?.anonymize_mode === "on" ? "всегда" : room.retention?.anonymize_mode === "off" ? "выключено" : "как в общих настройках"}</td></tr>
                </tbody></table>
              </fieldset>
              <fieldset className="group"><legend>Инструкция для протокола этой комнаты</legend>
                <label>Дополнение к общей инструкции <span className="muted small">(необязательно)</span>
                  <textarea rows={3} value={form.protocol_instructions} onChange={(e) => set("protocol_instructions", e.target.value)} maxLength={20000}
                            placeholder="Например: фиксируй решения и сроки по проектам, не выделяй обсуждение погоды" />
                  <span className="help">Добавляется к общей инструкции организации и показывается в окне «Сформировать протокол» — участник видит и может изменить её перед отправкой.</span></label>
              </fieldset>
              <h3 style={{ margin: "14px 0 4px" }}>Рассылка протоколов</h3>
              <DeliveryEditor roomId={roomId} spec={form.mail_delivery} onChange={(sp) => set("mail_delivery", sp)} />
              <p className="help">Эти значения действуют для каждой встречи этой комнаты по умолчанию. Для конкретной встречи руководитель может изменить их кнопкой «Эта встреча» в комнате.</p>
            </div>
          )}

          {tab === "model" && (
            <div role="tabpanel">
              <h3 style={{ margin: "0 0 6px" }}>Модель для протокола</h3>
              <LlmChoiceEditor scope="room" purpose="protocol" value={form.llm} onChange={(c) => set("llm", c)} options={room.llm_options} effective={room.llm_effective} />
              {room.llm_effective && form.llm.mode !== room.llm?.mode && <p className="muted small">Сейчас сохранено: {choiceLabel(room.llm ?? emptyChoice(), room.llm_options)}. Нажмите «Сохранить», чтобы применить выбор.</p>}
              <h3 style={{ margin: "18px 0 6px" }}>Модель для резюме</h3>
              <p className="help" style={{ marginTop: 0 }}>Протокол и краткое резюме — разные задачи, поэтому у каждой своя модель и своё наследование: система → комната → встреча.</p>
              <LlmChoiceEditor scope="room" purpose="summary" value={form.llm_summary} onChange={(c) => set("llm_summary", c)} options={room.llm_options} effective={room.llm_summary_effective} />
              {room.llm_summary_effective && form.llm_summary.mode !== room.llm_summary?.mode && <p className="muted small">Сейчас сохранено: {choiceLabel(room.llm_summary ?? emptyChoice(), room.llm_options)}. Нажмите «Сохранить», чтобы применить выбор.</p>}
            </div>
          )}

          {tab === "phone" && <TelephonyEditor sip={form.sip} options={room.sip_options} onChange={(sp) => set("sip", sp)} />}

          {note && <div className="alert ok" role="status">{note}</div>}
          {error && <div className="alert error" role="alert">{error}</div>}
          <div className="row form-actions"><button className="btn primary" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</button><button type="button" className="btn ghost" onClick={onClose}>Закрыть</button></div>
        </form>
      )}
    </Modal>
  );
}
