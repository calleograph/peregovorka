import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, type AclEntry, type AnonymizeMode, type ApiError, type ApiProfile, type HistoryAccess, type LlmChoice, type MailDeliverySpec, type RoomAdmin, type RoomManage, type RoomSip, type RoomType } from "../api";
import { copyText } from "../util";
import AclPicker from "./AclPicker";
import LiveSettings from "./LiveSettings";
import RoomIconPicker, { applyPendingIcon, type PendingIcon } from "./RoomIconPicker";
import DeliveryEditor, { emptySpec } from "./DeliveryEditor";
import LlmChoiceEditor from "./LlmChoiceEditor";
import TelephonyEditor from "./TelephonyEditor";
import InfoTip from "./InfoTip";
import { choiceLabel, emptyChoice } from "../phone";

/**
 * Единый редактор комнаты: создание и изменение, у администратора сервера и у руководителя комнаты — одни и те же вкладки и поля.
 * Администратор дополнительно видит адрес комнаты, включение, сроки хранения, доступ к истории и обезличивание (их меняет только он).
 * Сохранение: руководитель — /rooms/{id}/manage; администратор — ещё и /admin/rooms (системные поля); новая комната создаётся, затем настраивается тем же запросом.
 */
type TabId = "main" | "mode" | "access" | "live" | "materials" | "autosend" | "model" | "phone";
const TABS: [TabId, string][] = [["main", "Основное"], ["mode", "Встреча и запись"], ["access", "Доступ и гости"], ["live", "Трансляция"], ["materials", "Материалы"], ["autosend", "Автоотправка"], ["model", "Языковая модель"], ["phone", "Телефония"]];
const TEMP_HIDDEN: TabId[] = ["model", "phone", "materials", "autosend"];
const BOARD_OPTIONS: [string, string][] = [["auto", "Как принято для типа комнаты"], ["everyone", "Все участники"], ["speakers", "Руководители и те, кому дали слово"], ["leaders", "Только руководители, остальные смотрят"], ["private", "Доска только у руководителей (остальные её не видят)"]];
const SLUG = /^[a-z0-9][a-z0-9_-]{1,62}$/;
const emptySip = (): RoomSip => ({ mode: "off", profile_id: null, extension: null, allow_inbound: false, allow_outbound: false, contacts: [] });
const daysText = (d: number | null | undefined) => (d == null ? "бессрочно" : `${d} дн.`);
type RetKind = "text" | "audio" | "video";
/** Сроки хранения материалов: по каждому — общий срок сервера или свой у комнаты (пусто — бессрочно; 0 — удалять сразу после обработки). */
const RET_KINDS: [RetKind, string, string][] = [
  ["text", "Текст", "Стенограммы, протоколы, чат и доска. Общий срок сервера — Администрирование → Общие → «Сроки хранения материалов»."],
  ["audio", "Аудиозаписи", "Файлы участников и общая аудиозапись. Удаляются и локальные файлы, и копии во внешнем хранилище (SMB)."],
  ["video", "Видеозаписи", "Видеозаписи встреч и записи эфиров. Срок ссылки зрителя на запись эфира — отдельный (вкладка «Трансляция»): истечение ссылки файл не удаляет."],
];
const numOrNull = (v: string) => (v.trim() === "" ? null : Number(v));

export interface RoomForm {
  name: string; description: string; max_participants: number; password: string; clearPassword: boolean; welcome_message: string; mute_on_join: boolean; mask_profanity: boolean;
  room_type: RoomType; encrypt_storage: boolean; e2ee_mode: "off" | "full" | "trusted"; record_audio: boolean; auto_record: boolean; recording_mode: "audio" | "audio_video" | "off";
  video_mode: "off" | "manual" | "auto"; video_quality: "720p" | "1080p"; camera_allowed: boolean; screen_share_allowed: boolean; board_access: string;
  guest_access_enabled: boolean; acl: AclEntry[]; moderators: AclEntry[]; mail_delivery: MailDeliverySpec;
  protocol_instructions: string; llm: LlmChoice; llm_summary: LlmChoice; sip: RoomSip; auto_map_mode: "inherit" | "on" | "off";
  // только администратор сервера
  slug: string; is_enabled: boolean; history_access: HistoryAccess; text_retention_days: string; audio_retention_days: string; video_retention_days: string; retention_custom: Record<RetKind, boolean>; anonymize_mode: AnonymizeMode; anonymizer_profile_id: string;
}

export const newRoomForm = (): RoomForm => ({
  name: "", description: "", max_participants: 20, password: "", clearPassword: false, welcome_message: "", mute_on_join: false, mask_profanity: true,
  room_type: "regular", encrypt_storage: false, e2ee_mode: "off", record_audio: false, auto_record: false, recording_mode: "audio", video_mode: "off", video_quality: "720p",
  camera_allowed: true, screen_share_allowed: true, board_access: "auto", guest_access_enabled: false, acl: [], moderators: [], mail_delivery: emptySpec(),
  protocol_instructions: "", llm: emptyChoice(), llm_summary: emptyChoice(), sip: emptySip(), auto_map_mode: "inherit",
  slug: "", is_enabled: true, history_access: "admin", text_retention_days: "", audio_retention_days: "", video_retention_days: "", retention_custom: { text: false, audio: false, video: false }, anonymize_mode: "inherit", anonymizer_profile_id: "",
});

export const toRoomForm = (r: RoomManage, a?: RoomAdmin): RoomForm => ({
  ...newRoomForm(),
  name: r.name, description: r.description ?? "", max_participants: r.max_participants, welcome_message: r.welcome_message ?? "", mute_on_join: r.mute_on_join, mask_profanity: r.mask_profanity ?? true,
  room_type: r.room_type, encrypt_storage: !!r.encrypt_storage, e2ee_mode: r.e2ee_mode ?? "off", record_audio: r.record_audio, auto_record: r.auto_record, recording_mode: r.recording_mode ?? "audio",
  video_mode: r.video_mode ?? "off", video_quality: r.video_quality ?? "720p", camera_allowed: r.camera_allowed, screen_share_allowed: r.screen_share_allowed,
  board_access: r.board_access && r.board_access !== "auto" ? r.board_access : (r.board_allowed ? "auto" : "leaders"), guest_access_enabled: r.guest_access_enabled,
  acl: r.acl, moderators: r.moderators, mail_delivery: r.mail_delivery && (r.mail_delivery.enabled || r.mail_delivery.materials.length) ? r.mail_delivery : emptySpec(),
  protocol_instructions: r.protocol_instructions ?? "", llm: r.llm ?? emptyChoice(), llm_summary: r.llm_summary ?? emptyChoice(), sip: r.sip ?? emptySip(), auto_map_mode: r.auto_map_mode ?? "inherit",
  slug: r.slug, is_enabled: r.is_enabled, history_access: (a?.history_access ?? r.retention?.history_access ?? "admin") as HistoryAccess,
  text_retention_days: (a?.text_retention_days ?? r.retention?.text_days)?.toString() ?? "", audio_retention_days: (a?.audio_retention_days ?? r.retention?.audio_days)?.toString() ?? "",
  video_retention_days: (a?.video_retention_days ?? r.retention?.video_days)?.toString() ?? "",
  retention_custom: { text: !!(a?.text_retention_custom ?? r.retention?.custom?.text), audio: !!(a?.audio_retention_custom ?? r.retention?.custom?.audio), video: !!(a?.video_retention_custom ?? r.retention?.custom?.video) },
  anonymize_mode: (a?.anonymize_mode ?? r.retention?.anonymize_mode ?? "inherit") as AnonymizeMode, anonymizer_profile_id: a?.anonymizer_profile_id ?? r.retention?.anonymizer_profile_id ?? "",
});

/** Настройки, которые сохраняет руководитель комнаты (и администратор) — запрос /rooms/{id}/manage. */
export function managePayload(f: RoomForm): Record<string, unknown> {
  return {
    name: f.name.trim(), description: f.description.trim() || null, max_participants: f.max_participants, welcome_message: f.welcome_message.trim() || null, mute_on_join: f.mute_on_join,
    mask_profanity: f.mask_profanity, room_type: f.room_type, record_audio: f.record_audio || f.auto_record, auto_record: f.auto_record, recording_mode: f.recording_mode,
    video_mode: f.video_mode, video_quality: f.video_quality, encrypt_storage: f.encrypt_storage, e2ee_mode: f.e2ee_mode, camera_allowed: f.camera_allowed, screen_share_allowed: f.screen_share_allowed,
    board_allowed: f.board_access !== "private" && f.board_access !== "leaders", board_access: f.board_access, guest_access_enabled: f.guest_access_enabled, acl: f.acl, moderators: f.moderators,
    mail_delivery: f.mail_delivery, protocol_instructions: f.protocol_instructions.trim() || null, llm: f.llm, llm_summary: f.llm_summary, sip: f.sip, auto_map_mode: f.auto_map_mode,
    ...(f.clearPassword ? { password: "" } : f.password ? { password: f.password } : {}),
  };
}

/** Системные поля комнаты — только администратор сервера (запрос /admin/rooms). */
export function adminPayload(f: RoomForm): Record<string, unknown> {
  return {
    slug: f.slug.trim().toLowerCase(), is_enabled: f.is_enabled, history_access: f.history_access,
    // срок: свой у комнаты (значение; пусто — бессрочно) или общий срок сервера
    ...Object.fromEntries((["text", "audio", "video"] as const).flatMap((k) => [[`${k}_retention_custom`, f.retention_custom[k]], ...(f.retention_custom[k] ? [[`${k}_retention_days`, numOrNull(f[`${k}_retention_days`])]] : [])])),
    anonymize_mode: f.anonymize_mode, anonymizer_profile_id: f.anonymizer_profile_id || null,
  };
}

/** Поле с коротким названием и подсказкой ⓘ. */
function F({ title, tip, children, className }: { title: ReactNode; tip?: ReactNode; children: ReactNode; className?: string }) {
  return <label className={className}><span className="field-title">{title}{tip && <InfoTip>{tip}</InfoTip>}</span>{children}</label>;
}
function Check({ checked, onChange, title, tip, disabled }: { checked: boolean; onChange: (v: boolean) => void; title: ReactNode; tip?: ReactNode; disabled?: boolean }) {
  return <label className="check"><input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /><span className="check-body"><span className="field-title">{title}{tip && <InfoTip>{tip}</InfoTip>}</span></span></label>;
}

export default function RoomEditor({ roomId, admin: adminProp, adminRoom, onClose, onSaved, onCreated }: {
  roomId?: string; admin?: boolean; adminRoom?: RoomAdmin; onClose: () => void; onSaved?: (r: RoomManage) => void; onCreated?: (id: string) => void;
}) {
  const [room, setRoom] = useState<RoomManage | null>(null);
  const [tpl, setTpl] = useState<Awaited<ReturnType<typeof api.admin.roomTemplate>> | null>(null);
  const [form, setForm] = useState<RoomForm | null>(null);
  const [origSlug, setOrigSlug] = useState("");
  const [tab, setTab] = useState<TabId>("main");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [anonProfiles, setAnonProfiles] = useState<ApiProfile[]>([]);
  const [pendingIcon, setPendingIcon] = useState<PendingIcon>(null);
  const isNew = !roomId;
  const admin = adminProp ?? (isNew || !!room?.can_edit_system_fields);       // администратор сервера — по правам из ответа сервера

  useEffect(() => {
    let alive = true;
    setError(""); setForm(null); setRoom(null);
    if (roomId) api.manage.get(roomId).then((r) => { if (alive) { setRoom(r); setForm(toRoomForm(r, adminRoom)); setOrigSlug(r.slug); } }).catch((e) => { if (alive) setError((e as ApiError).message || "Не удалось загрузить настройки"); });
    else api.admin.roomTemplate().then((t) => { if (alive) { setTpl(t); setForm(newRoomForm()); } }).catch((e) => { if (alive) setError((e as ApiError).message); });
    return () => { alive = false; };
  }, [roomId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (admin) void api.admin.profiles("anonymizer").then(setAnonProfiles).catch(() => undefined); }, [admin]);

  const set = <K extends keyof RoomForm>(k: K, v: RoomForm[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));
  const opts = room ?? tpl;
  const temporary = room?.lifetime === "temporary";
  const host = window.location.host;

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form || busy) return;
    setError(""); setNote("");
    if (!form.name.trim()) { setTab("main"); setError("Укажите название комнаты."); return; }
    const slug = form.slug.trim().toLowerCase();
    if (admin && !SLUG.test(slug)) { setTab("main"); setError("Адрес комнаты: латиница, цифры, «-» и «_», 2–63 символа, начинается с буквы или цифры."); return; }
    if (form.sip.mode !== "off" && form.sip.allow_inbound && !form.sip.extension) { setTab("phone"); setError("Для входящих звонков укажите внутренний номер комнаты."); return; }
    if (admin && !isNew && slug !== origSlug && !window.confirm(`Адрес комнаты изменится: ${host}/rooms/${origSlug} → ${host}/rooms/${slug}.\n\nСтарая ссылка продолжит работать и перенаправит на новую. Гостевая ссылка не изменится. Сменить адрес?`)) return;
    setBusy(true);
    try {
      let id = roomId;
      if (isNew) {
        const created = await api.admin.createRoom({ ...managePayload(form), ...adminPayload(form), password: form.password || null, transcription_enabled: true });
        id = created.id;
        if (pendingIcon) await applyPendingIcon(id, pendingIcon).catch((err) => setError(`Комната создана, но значок не применился: ${(err as ApiError).message}`));
      } else if (admin) {
        await api.admin.patchRoom(id!, adminPayload(form));
      }
      const r = await api.manage.patch(id!, managePayload(form));                       // все настройки комнаты — одним запросом, одинаково при создании и изменении
      setRoom(r); setForm(toRoomForm(r, admin ? { ...(adminRoom ?? {}), ...adminPayload(form) } as RoomAdmin : undefined)); setOrigSlug(r.slug); onSaved?.(r);
      if (isNew) { onCreated?.(r.id); return; }
      setNote(r.needs_rejoin ? "Сохранено. Тип комнаты, камера и показ экрана у тех, кто уже на встрече, изменятся при следующем входе." : "Сохранено.");
    } catch (err) { setError((err as ApiError).message || "Не удалось сохранить"); }
    setBusy(false);
  };

  const guestAct = async (action: "rotate" | "revoke") => {
    if (!roomId) return;
    const q = action === "rotate" ? "Выпустить новую гостевую ссылку? Прежняя перестанет работать, гости на встрече будут отключены." : "Отозвать гостевую ссылку? Гостевой доступ выключится, гости на встрече будут отключены.";
    if (!window.confirm(q)) return;
    setBusy(true); setError(""); setNote("");
    try { const r = await api.manage.guestLink(roomId, action); setRoom(r); set("guest_access_enabled", r.guest_access_enabled); setNote(action === "rotate" ? "Новая ссылка выпущена." : "Ссылка отозвана."); }
    catch (err) { setError((err as ApiError).message); }
    setBusy(false);
  };

  if (!form) return error ? <div className="alert error" role="alert">{error}</div> : <p className="muted">Загрузка…</p>;
  const url = room?.guest_token ? `${window.location.origin}/guest/${room.guest_token}` : "";
  const tabs = TABS.filter(([id]) => !temporary || !TEMP_HIDDEN.includes(id));
  const needsRoom = (what: string) => <p className="muted small">{what} станет доступно после создания комнаты: нажмите «Создать» — редактор останется открытым.</p>;

  return (
    <form className="form room-form" onSubmit={save} noValidate>
      {temporary && <p className="muted small" style={{ marginTop: 0 }}>Временная переговорка: модель, телефония и рассылка материалов берутся из системных настроек.</p>}
      <div className="tabs" role="tablist" aria-label="Разделы настроек комнаты">
        {tabs.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {tab === "main" && (
        <div role="tabpanel">
          <div className="cols">
            <F title="Название"><input value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={200} placeholder="Переговорная «Север»" /></F>
            {admin && (
              <F title="Адрес комнаты" tip={<>Латиница, цифры, «-» и «_»; уникален. Ссылка: <code>{host}/rooms/{form.slug.trim() || "…"}</code>. При смене старый адрес продолжит работать и перенаправит на новый; гостевая ссылка не меняется.{room && (adminRoom?.slug_history?.length ?? 0) > 0 && <> Прежние адреса: {adminRoom?.slug_history?.join(", ")}.</>}</>}>
                <input value={form.slug} onChange={(e) => set("slug", e.target.value.toLowerCase())} placeholder="it-1" spellCheck={false} />
                {!isNew && form.slug.trim().toLowerCase() !== origSlug && <span className="help" style={{ color: "var(--danger-text)" }}>Ссылка изменится: прежняя ({origSlug}) будет перенаправлять на новую.</span>}
              </F>
            )}
          </div>
          <F title="Описание"><input value={form.description} onChange={(e) => set("description", e.target.value)} maxLength={2000} placeholder="Еженедельная планёрка отдела" /></F>
          <div className="cols">
            <F title="Максимум участников" tip="Больше людей войти не смогут."><input type="number" min={1} max={2000} value={form.max_participants} onChange={(e) => set("max_participants", Number(e.target.value))} /></F>
            <F title={<>Пароль комнаты{room?.has_password && <span className="muted small"> (пусто — не менять)</span>}</>} tip="Необязательно: участники вводят его при входе дополнительно к учётной записи.">
              <input type="password" value={form.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" disabled={form.clearPassword} /></F>
          </div>
          {room?.has_password && <Check checked={form.clearPassword} onChange={(v) => set("clearPassword", v)} title="Убрать пароль" />}
          {admin && <Check checked={form.is_enabled} onChange={(v) => set("is_enabled", v)} title="Комната включена" tip="Выключенная комната не показывается в списке и недоступна для входа." />}
          <Check checked={form.mute_on_join} onChange={(v) => set("mute_on_join", v)} title="Микрофон при входе выключен" tip="Участники заходят без звука и включают микрофон сами. Удобно для больших встреч." />
          <Check checked={form.mask_profanity} onChange={(v) => set("mask_profanity", v)} title="Скрывать нецензурную лексику в стенограмме" tip="Слова заменяются звёздочками в стенограмме, субтитрах, истории, выгрузках и письмах. Звук и записи не меняются; действует и для прежних встреч." />
          <F title={<>Приветствие при входе <span className="muted small">(необязательно)</span></>}><textarea rows={2} value={form.welcome_message} onChange={(e) => set("welcome_message", e.target.value)} maxLength={2000} placeholder="Добро пожаловать! Ведутся запись и стенограмма." /></F>
          <RoomIconPicker roomId={roomId} name={form.name} icon={room?.icon} onChange={(i) => { if (room) { const nr = { ...room, icon: i }; setRoom(nr); onSaved?.(nr); } }} pending={pendingIcon} onPending={setPendingIcon} />
        </div>
      )}

      {tab === "mode" && (
        <div role="tabpanel">
          <fieldset className="group"><legend>Тип комнаты</legend>
            {([["regular", "Обычная", "Все участники могут говорить, включать камеру и показывать экран (если разрешено ниже)."],
              ["presentation", "Презентационная", "Участники входят слушателями; говорят руководители и те, кому руководитель «дал слово» — до конца встречи."],
              ["broadcast", "Трансляционная", "Выступающие общаются в конференции, зрители смотрят прямой эфир по отдельной ссылке. Настройки эфира — на вкладке «Трансляция»."]] as const).map(([v, l, tip]) => (
              <label key={v} className="check"><input type="radio" name="rt" checked={form.room_type === v} onChange={() => set("room_type", v)} /><span className="check-body"><span className="field-title">{l}<InfoTip>{tip}</InfoTip></span></span></label>
            ))}
          </fieldset>
          <fieldset className="group"><legend>Аудиозапись и стенограмма</legend>
            <Check checked={form.record_audio || form.auto_record} disabled={form.auto_record} onChange={(v) => set("record_audio", v)} title="Разрешить аудиозапись"
                   tip="Руководитель включает и выключает запись звука кнопкой во время встречи. Стенограмма ведётся всегда; руководитель может приостановить её на встрече." />
            <Check checked={form.auto_record} onChange={(v) => set("auto_record", v)} title="Начинать аудиозапись автоматически" tip="Запись начинается вместе со встречей; иначе её включают вручную." />
            <F title="Общая аудиозапись встречи" tip={<>После встречи в «Истории» появится одна общая запись всех участников с паузами и одновременной речью.{form.recording_mode === "off" && " Файлы участников для расшифровки сохраняются в любом случае."}</>}>
              <select value={form.recording_mode === "audio_video" ? "audio" : form.recording_mode} onChange={(e) => set("recording_mode", e.target.value as RoomForm["recording_mode"])}>
                <option value="audio">Собирать</option><option value="off">Не собирать</option>
              </select></F>
          </fieldset>
          <fieldset className="group"><legend>Видеозапись</legend>
            {opts?.video_enabled === false && <p className="help" style={{ marginTop: 0 }}>Видеозапись выключена администратором сервера (Администрирование → Видеозапись). Режим можно выбрать заранее.</p>}
            <div className="cols">
              <F title="Режим" tip={<>Один MP4 со всей встречей: сетка участников, при показе экрана он на основном месте. Не зависит от аудиозаписи. Пустая комната не записывается. Недоступна при сквозном шифровании и шифровании хранилища.{form.room_type === "presentation" && form.video_mode === "off" && " Для презентации обычно выбирают «Автоматически»."}</>}>
                <select value={form.video_mode} onChange={(e) => set("video_mode", e.target.value as RoomForm["video_mode"])}>
                  <option value="off">Выключена</option><option value="manual">Вручную (руководитель)</option><option value="auto">Автоматически, пока есть участники</option>
                </select></F>
              {form.video_mode !== "off" && (
                <F title="Качество" tip="720p ≈ 1,2 ГБ в час, 1080p ≈ 2 ГБ в час (расчётно); в 1080p мелкий текст на экране читается лучше.">
                  <select value={form.video_quality} onChange={(e) => set("video_quality", e.target.value as RoomForm["video_quality"])}><option value="720p">720p</option><option value="1080p">1080p</option></select></F>
              )}
            </div>
          </fieldset>
          <fieldset className="group"><legend>Что доступно участникам</legend>
            <div className="checks">
              <Check checked={form.camera_allowed} onChange={(v) => set("camera_allowed", v)} title="Камера" />
              <Check checked={form.screen_share_allowed} onChange={(v) => set("screen_share_allowed", v)} title="Показ экрана" />
            </div>
            <F title="Общая доска" tip="Руководители рисуют всегда. «Только у руководителей» — для закрытых презентаций: участники не видят доску. Слово (микрофон) право рисовать не даёт, если не выбран вариант «и те, кому дали слово». В презентации права участников действуют, пока у них есть слово.">
              <select value={form.board_access} onChange={(e) => set("board_access", e.target.value)}>{BOARD_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></F>
          </fieldset>
          <fieldset className="group"><legend>Шифрование</legend>
            <Check checked={form.encrypt_storage} onChange={(v) => set("encrypt_storage", v)} title="Шифровать чат и стенограммы при хранении"
                   tip="Новые сообщения и реплики хранятся в базе зашифрованными (AES-256-GCM, ключи на сервере). Уже записанное не меняется — для него «Зашифровать существующие» в разделе «Шифрование хранилища». Это защита хранилища, а не звука и видео." />
            <F title="Сквозное шифрование звука и видео" tip="Звук и видео шифруются на устройствах участников; сервер звонков содержимого не видит. «С доверенным компонентом»: служба распознавания получает ключ встречи — стенограмма и запись работают. «Полное»: ключа у сервера нет, стенограмма и запись недоступны. Не работает с телефонными участниками. Действует со следующей встречи.">
              <select value={form.e2ee_mode} onChange={(e) => set("e2ee_mode", e.target.value as RoomForm["e2ee_mode"])}>
                <option value="off">Выключено</option><option value="trusted">Включено, с доверенным компонентом</option><option value="full">Полное (без стенограммы и записи)</option>
              </select></F>
          </fieldset>
        </div>
      )}

      {tab === "access" && (
        <div role="tabpanel">
          <AclPicker acl={form.acl} moderators={form.moderators} onChange={(n) => setForm((f) => (f ? { ...f, ...n } : f))}
                     search={(kind, q) => (roomId && !admin ? api.manage.search(roomId, kind, q) : api.admin.search(kind, q))}
                     leaderEmpty="Руководителей нет — комнатой смогут управлять только администраторы сервера." />
          {admin && (
            <F title="Кто видит встречу после завершения" tip="«Участники» оставляет им доступ к стенограмме и протоколам через «Историю». Отдельным людям доступ можно выдать на странице встречи.">
              <select value={form.history_access} onChange={(e) => set("history_access", e.target.value as HistoryAccess)}>
                <option value="admin">Администраторы и руководители</option><option value="participants">Администраторы, руководители и все участники встречи</option>
              </select></F>
          )}
          <fieldset className="group"><legend>Гостевой доступ</legend>
            <Check checked={form.guest_access_enabled} onChange={(v) => set("guest_access_enabled", v)} title="Разрешить вход по гостевой ссылке"
                   tip="Гость вводит имя и попадает в идущую встречу как «Имя (гость)»: без истории, стенограммы и показа экрана. Выключение отключает гостей, ссылка сохраняется." />
            {room?.guest_access_enabled && url ? (
              <div className="guest-link">
                <div className="row tight"><input readOnly value={url} onFocus={(e) => e.currentTarget.select()} aria-label="Гостевая ссылка" />
                  <button type="button" className="btn mini" onClick={async () => setNote((await copyText(url)) ? "Ссылка скопирована." : "Не удалось скопировать — выделите ссылку вручную.")}>Копировать</button></div>
                <div className="row tight">
                  <button type="button" className="btn mini" disabled={busy} onClick={() => void guestAct("rotate")}>Выпустить новую ссылку</button>
                  <button type="button" className="btn mini danger" disabled={busy} onClick={() => void guestAct("revoke")}>Отозвать ссылку</button>
                </div>
              </div>
            ) : <p className="muted small">{isNew ? "Ссылка появится после создания комнаты." : room?.guest_access_enabled ? "" : "Включите и сохраните — ссылка появится здесь."}</p>}
          </fieldset>
        </div>
      )}

      {tab === "live" && (roomId && room
        ? <LiveSettings roomId={roomId} roomSlug={room.slug} isBroadcastType={form.room_type === "broadcast"} hasSpeakerPassword={room.has_password}
                        onSpeakerPassword={async (pw) => { const r = await api.manage.patch(roomId, { password: pw }); setRoom(r); onSaved?.(r); }} />
        : <div role="tabpanel">{needsRoom("Настройка эфира (ссылки зрителей, пароли, площадки)")}</div>)}

      {tab === "materials" && (
        <div role="tabpanel">
          <fieldset className="group"><legend>Хранение</legend>
            {admin ? (
              <div className="ret-grid">
                {RET_KINDS.map(([k, label, tip]) => {
                  const server = room?.retention?.server?.[k];
                  const custom = form.retention_custom[k];
                  return (
                    <div key={k} className="ret-row">
                      <span className="field-title">{label}<InfoTip>{tip}</InfoTip></span>
                      <select value={custom ? "own" : "server"} onChange={(e) => set("retention_custom", { ...form.retention_custom, [k]: e.target.value === "own" })} aria-label={`${label}: срок`}>
                        <option value="server">Общий срок сервера{room?.retention?.server ? ` — ${daysText(server ?? null)}` : ""}</option>
                        <option value="own">Свой срок комнаты</option>
                      </select>
                      {custom && <input type="number" min={0} value={form[`${k}_retention_days`]} onChange={(e) => set(`${k}_retention_days`, e.target.value)} placeholder="бессрочно" aria-label={`${label}: дней`} />}
                      {custom && <span className="muted small">дней</span>}
                    </div>);
                })}
              </div>
            ) : (
              <table className="table compact"><tbody>
                <tr><td>Текст встреч</td><td>{daysText(room?.retention?.text_days)}</td></tr>
                <tr><td>Аудиозаписи</td><td>{room?.record_audio || form.auto_record ? daysText(room?.retention?.audio_days) : "запись не ведётся"}</td></tr>
                <tr><td>Видеозаписи и записи эфиров</td><td>{daysText(room?.retention?.video_days)}</td></tr>
                <tr><td>Кто видит завершённую встречу</td><td>{room?.retention?.history_access === "participants" ? "участники встречи" : "руководители и администраторы"}</td></tr>
              </tbody></table>
            )}
            <F title="Карта разговора после встречи" tip="Карта строится локальной моделью несколько минут и нагружает сервер, поэтому по умолчанию создаётся вручную — кнопкой в истории встречи.">
              <select value={form.auto_map_mode} onChange={(e) => set("auto_map_mode", e.target.value as RoomForm["auto_map_mode"])}>
                <option value="inherit">Как в общих настройках</option><option value="on">Формировать автоматически</option><option value="off">Не формировать автоматически</option>
              </select></F>
          </fieldset>
          <F title={<>Дополнение к инструкции протокола <span className="muted small">(необязательно)</span></>} tip="Добавляется к общей инструкции организации и показывается в окне «Сформировать протокол» — его можно изменить перед отправкой.">
            <textarea rows={3} value={form.protocol_instructions} onChange={(e) => set("protocol_instructions", e.target.value)} maxLength={20000} placeholder="Например: фиксируй решения и сроки по проектам" /></F>
        </div>
      )}

      {tab === "autosend" && (
        <div role="tabpanel">
          <DeliveryEditor roomId={roomId} spec={form.mail_delivery} onChange={(sp) => set("mail_delivery", sp)} localModel={form.llm.mode === "local" || (form.llm.mode === "inherit" && opts?.llm_options?.system.provider === "local")} />
          <p className="muted small">Значения действуют для каждой встречи комнаты; для конкретной встречи руководитель меняет их кнопкой «Эта встреча»<InfoTip>Сохраняются кнопкой «Сохранить» и записываются в журнал аудита (кто и что изменил).</InfoTip></p>
        </div>
      )}

      {tab === "model" && (
        <div role="tabpanel">
          <h3 style={{ margin: "0 0 6px" }}>Модель для протокола</h3>
          <LlmChoiceEditor scope="room" purpose="protocol" value={form.llm} onChange={(c) => set("llm", c)} options={opts?.llm_options} effective={opts?.llm_effective} />
          {room?.llm_effective && form.llm.mode !== room.llm?.mode && <p className="muted small">Сейчас сохранено: {choiceLabel(room.llm ?? emptyChoice(), room.llm_options)}.</p>}
          <h3 style={{ margin: "14px 0 6px" }} className="field-title">Модель для резюме<InfoTip>Протокол и краткое резюме — разные задачи: у каждой своя модель и своё наследование «система → комната → встреча».</InfoTip></h3>
          <LlmChoiceEditor scope="room" purpose="summary" value={form.llm_summary} onChange={(c) => set("llm_summary", c)} options={opts?.llm_options} effective={opts?.llm_summary_effective} />
          {admin && (
            <fieldset className="group"><legend>Обезличивание</legend>
              <div className="cols">
                <F title="Перед отправкой во внешнюю модель" tip="Выключение не мешает протоколам: в модель уходит исходная стенограмма (с именами и данными участников). Выключайте, если модель внутренняя или данные не конфиденциальны.">
                  <select value={form.anonymize_mode} onChange={(e) => set("anonymize_mode", e.target.value as AnonymizeMode)}>
                    <option value="inherit">Как в общих настройках</option><option value="on">Всегда (сервис недоступен — протокол не создаётся)</option><option value="off">Не обезличивать</option>
                  </select></F>
                <F title="Сервис обезличивания" tip="Профили — в разделе «Обезличивание».">
                  <select value={form.anonymizer_profile_id} onChange={(e) => set("anonymizer_profile_id", e.target.value)} disabled={form.anonymize_mode === "off"}>
                    <option value="">По умолчанию (общий)</option>{anonProfiles.filter((p) => !p.virtual).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select></F>
              </div>
            </fieldset>
          )}
        </div>
      )}

      {tab === "phone" && <TelephonyEditor sip={form.sip} options={opts?.sip_options} onChange={(sp) => set("sip", sp)} />}

      {note && <div className="alert ok" role="status">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="row form-actions">
        <button className="btn primary" disabled={busy}>{busy ? "Сохранение…" : isNew ? "Создать" : "Сохранить"}</button>
        <button type="button" className="btn ghost" onClick={onClose}>{isNew ? "Отмена" : "Закрыть"}</button>
      </div>
    </form>
  );
}
