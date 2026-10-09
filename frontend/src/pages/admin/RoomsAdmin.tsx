import { FormEvent, useCallback, useEffect, useState } from "react";
import { copyText } from "../../util";
import { api, type AclEntry, type AnonymizeMode, type ApiError, type ApiProfile, type HistoryAccess, type RoomAdmin, type RoomType } from "../../api";
import AclPicker, { entryLabel } from "../../components/AclPicker";

interface Form {
  id?: string; slug: string; name: string; description: string; is_enabled: boolean; max_participants: number;
  password: string; clearPassword: boolean; transcription_enabled: boolean; record_audio: boolean;
  camera_allowed: boolean; screen_share_allowed: boolean; text_retention_days: string; audio_retention_days: string;
  protocol_instructions: string; acl: AclEntry[]; moderators: AclEntry[]; history_access: HistoryAccess;
  anonymize_mode: AnonymizeMode; llm_profile_id: string; anonymizer_profile_id: string; mute_on_join: boolean; welcome_message: string;
  guest_access_enabled: boolean; guest_token: string | null; room_type: RoomType; auto_record: boolean; board_allowed: boolean; slug_history?: string[];
}

const empty: Form = {
  slug: "", name: "", description: "", is_enabled: true, max_participants: 20, password: "", clearPassword: false,
  transcription_enabled: true, record_audio: false, camera_allowed: true, screen_share_allowed: true,
  text_retention_days: "", audio_retention_days: "", protocol_instructions: "", acl: [], moderators: [], history_access: "admin",
  anonymize_mode: "inherit", llm_profile_id: "", anonymizer_profile_id: "", mute_on_join: false, welcome_message: "",
  guest_access_enabled: false, guest_token: null, room_type: "regular", auto_record: false, board_allowed: true,
};

const days = (v: string) => (v.trim() === "" ? null : Number(v));
const SLUG = /^[a-z0-9][a-z0-9_-]{1,62}$/;
const host = () => window.location.host;

function toForm(r: RoomAdmin): Form {
  return { id: r.id, slug: r.slug, name: r.name, description: r.description ?? "", is_enabled: r.is_enabled,
    max_participants: r.max_participants, password: "", clearPassword: false, transcription_enabled: r.transcription_enabled,
    record_audio: r.record_audio, camera_allowed: r.camera_allowed, screen_share_allowed: r.screen_share_allowed,
    text_retention_days: r.text_retention_days?.toString() ?? "", audio_retention_days: r.audio_retention_days?.toString() ?? "",
    protocol_instructions: r.protocol_instructions ?? "", acl: r.acl, moderators: r.moderators ?? [], history_access: r.history_access ?? "admin",
    anonymize_mode: r.anonymize_mode ?? "inherit", llm_profile_id: r.llm_profile_id ?? "", anonymizer_profile_id: r.anonymizer_profile_id ?? "",
    mute_on_join: r.mute_on_join ?? false, welcome_message: r.welcome_message ?? "",
    guest_access_enabled: r.guest_access_enabled ?? false, guest_token: r.guest_token ?? null,
    room_type: r.room_type ?? "regular", auto_record: r.auto_record ?? false, board_allowed: r.board_allowed ?? true, slug_history: r.slug_history ?? [] };
}

type TabId = "main" | "access" | "features" | "ai" | "storage";
const TABS: [TabId, string][] = [["main", "Основное"], ["access", "Доступ и руководители"], ["features", "Возможности"], ["ai", "Нейросети и протоколы"], ["storage", "Хранение"]];

/** Гостевая ссылка комнаты: копирование, перевыпуск (старая перестаёт работать) и отзыв (гости отключаются). Действия применяются сразу. */
function GuestLink({ form, onChange }: { form: Form; onChange: (r: RoomAdmin) => void }) {
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const url = form.guest_token ? `${window.location.origin}/guest/${form.guest_token}` : "";
  const act = async (action: "rotate" | "revoke") => {
    const q = action === "rotate"
      ? "Выпустить новую гостевую ссылку? Прежняя перестанет работать, гости, которые сейчас на встрече, будут отключены."
      : "Отозвать гостевую ссылку? Гостевой доступ будет выключен, гости, которые сейчас на встрече, будут отключены.";
    if (!form.id || !window.confirm(q)) return;
    setBusy(true); setMsg("");
    try { onChange(await api.admin.guestLink(form.id, action)); setMsg(action === "rotate" ? "Новая ссылка выпущена." : "Ссылка отозвана."); }
    catch (e) { setMsg((e as ApiError).message); }
    setBusy(false);
  };
  if (!form.id) return <p className="muted small">Ссылка появится после сохранения комнаты.</p>;
  if (!form.guest_access_enabled || !url) return <p className="muted small">Гостевой доступ выключен — по ссылке войти нельзя. Включите и сохраните — ссылка появится здесь.</p>;
  return (
    <div className="guest-link">
      <div className="row tight"><input readOnly value={url} onFocus={(e) => e.currentTarget.select()} aria-label="Гостевая ссылка" />
        <button type="button" className="btn mini" onClick={async () => setMsg((await copyText(url)) ? "Ссылка скопирована." : "Не удалось скопировать — выделите ссылку вручную.")}>Копировать</button></div>
      <div className="row tight">
        <button type="button" className="btn mini" disabled={busy} onClick={() => void act("rotate")}>Выпустить новую ссылку</button>
        <button type="button" className="btn mini danger" disabled={busy} onClick={() => void act("revoke")}>Отозвать ссылку</button>
        {msg && <span className="muted small" role="status">{msg}</span>}
      </div>
    </div>
  );
}

export default function RoomsAdmin() {
  const [rooms, setRooms] = useState<RoomAdmin[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [tab, setTab] = useState<TabId>("main");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [llmProfiles, setLlmProfiles] = useState<ApiProfile[]>([]);
  const [anonProfiles, setAnonProfiles] = useState<ApiProfile[]>([]);
  const [showClosed, setShowClosed] = useState(false);
  const [origSlug, setOrigSlug] = useState("");
  useEffect(() => {
    if (!form) return;
    void api.admin.profiles("llm").then(setLlmProfiles).catch(() => undefined);
    void api.admin.profiles("anonymizer").then(setAnonProfiles).catch(() => undefined);
  }, [form === null]);  // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(() => api.admin.rooms(showClosed).then(setRooms).catch((e) => setError(e.message)), [showClosed]);
  useEffect(() => { void load(); }, [load]);

  const open = (f: Form) => { setForm(f); setOrigSlug(f.slug); setTab("main"); setError(""); };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setError(""); setNote("");
    if (!form.name.trim()) { setTab("main"); setError("Укажите название комнаты."); return; }
    const slug = form.slug.trim().toLowerCase();
    if (!SLUG.test(slug)) { setTab("main"); setError("Адрес комнаты: латиница, цифры, «-» и «_», 2–63 символа, начинается с буквы или цифры."); return; }
    if (form.id && slug !== origSlug && !window.confirm(`Адрес комнаты изменится: ${host()}/rooms/${origSlug} → ${host()}/rooms/${slug}.\n\nСтарая ссылка продолжит работать и перенаправит на новую. Гостевая ссылка не изменится. Сменить адрес?`)) return;
    const base = {
      name: form.name, description: form.description || null, is_enabled: form.is_enabled, max_participants: form.max_participants,
      transcription_enabled: true, record_audio: form.record_audio || form.auto_record, camera_allowed: form.camera_allowed,
      room_type: form.room_type, auto_record: form.auto_record, board_allowed: form.board_allowed,
      screen_share_allowed: form.screen_share_allowed, text_retention_days: days(form.text_retention_days),
      audio_retention_days: days(form.audio_retention_days), protocol_instructions: form.protocol_instructions || null,
      acl: form.acl, moderators: form.moderators, history_access: form.history_access,
      anonymize_mode: form.anonymize_mode, llm_profile_id: form.llm_profile_id || null, anonymizer_profile_id: form.anonymizer_profile_id || null,
      mute_on_join: form.mute_on_join, welcome_message: form.welcome_message.trim() || null,
      guest_access_enabled: form.guest_access_enabled,
    };
    try {
      if (form.id) {
        await api.admin.patchRoom(form.id, { ...base, slug: form.slug.trim().toLowerCase(), ...(form.clearPassword ? { password: "" } : form.password ? { password: form.password } : {}) });
      } else {
        await api.admin.createRoom({ ...base, slug: form.slug.trim().toLowerCase(), password: form.password || null });
      }
      setForm(null); setNote("Сохранено"); await load();
    } catch (err) { setError((err as ApiError).message); }
  };

  const remove = async (r: RoomAdmin) => {
    if (!window.confirm(`Удалить комнату «${r.name}» вместе с историей встреч?`)) return;
    try { await api.admin.deleteRoom(r.id); await load(); } catch (err) { setError((err as ApiError).message); }
  };

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  return (
    <section>
      <div className="row"><h2>Переговорки</h2><div className="spacer" />
        <label className="check small" style={{ margin: 0 }}><input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> показывать закрытые временные</label>
        {!form && <button className="btn primary" onClick={() => open({ ...empty })}>＋ Создать комнату</button>}</div>
      {note && <div className="alert ok">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}

      {form && (
        <form className="card form room-form" onSubmit={save} noValidate>
          <div className="row"><h3 style={{ margin: 0 }}>{form.id ? `Изменение комнаты «${form.name}»` : "Новая комната"}</h3></div>
          <div className="tabs" role="tablist" aria-label="Разделы настроек комнаты">
            {TABS.map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>)}
          </div>

          {tab === "main" && (
            <div role="tabpanel">
              <div className="cols">
                <label>Название<input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Переговорная «Север»" />
                  <span className="example">Пример: <code>ИТ-1</code></span></label>
                <label>Технический идентификатор / адрес комнаты<input value={form.slug} onChange={(e) => set("slug", e.target.value.toLowerCase())} placeholder="meeting-room-1" spellCheck={false} />
                  <span className="help">Латиница, цифры, «-» и «_»; регистр не важен. Уникален. Попадает в ссылку на комнату.</span>
                  <span className="help">Адрес: <code>{host()}/rooms/{form.slug.trim() || "…"}</code></span>
                  {form.id && form.slug.trim().toLowerCase() !== origSlug && <span className="help" style={{ color: "var(--danger-text)" }}>Постоянная ссылка изменится. Старая ({origSlug}) продолжит работать и перенаправит на новую; ссылки с UUID тоже работают. Гостевая ссылка не изменится.</span>}
                  {form.id && (form.slug_history?.length ?? 0) > 0 && <span className="help">Прежние адреса (перенаправляют на нынешний): {form.slug_history?.join(", ")}</span>}
                  <span className="example">Пример: <code>it-1</code></span></label>
              </div>
              <label>Описание<input value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="Еженедельная планёрка отдела" /></label>
              <div className="cols">
                <label>Максимум участников<input type="number" min={1} max={200} value={form.max_participants} onChange={(e) => set("max_participants", Number(e.target.value))} />
                  <span className="help">Больше людей войти не смогут.</span></label>
                <label>Пароль комнаты {form.id && <span className="muted small">(пусто — не менять)</span>}
                  <input type="password" value={form.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" disabled={form.clearPassword} />
                  <span className="help">Необязательно: участники вводят его при входе дополнительно к доменной учётной записи.</span></label>
              </div>
              {form.id && <label className="check"><input type="checkbox" checked={form.clearPassword} onChange={(e) => set("clearPassword", e.target.checked)} /> Убрать пароль</label>}
              <label className="check"><input type="checkbox" checked={form.is_enabled} onChange={(e) => set("is_enabled", e.target.checked)} /> <span className="check-body">Комната включена<span className="help">Выключенная комната не показывается в списке и недоступна для входа.</span></span></label>
              <label>Приветствие при входе <span className="muted small">(необязательно)</span>
                <textarea rows={2} value={form.welcome_message} onChange={(e) => set("welcome_message", e.target.value)} maxLength={2000} placeholder="Добро пожаловать! Запись и транскрибация ведутся." />
                <span className="help">Показывается участнику сразу после входа: правила встречи, напоминание о записи и т. п.</span></label>
            </div>
          )}

          {tab === "access" && (
            <div role="tabpanel">
              <AclPicker acl={form.acl} moderators={form.moderators} onChange={(n) => setForm((f) => (f ? { ...f, ...n } : f))} search={(kind, q) => api.admin.search(kind, q)} />
              <label>Кто видит встречу после её завершения
                <select value={form.history_access} onChange={(e) => set("history_access", e.target.value as HistoryAccess)}>
                  <option value="admin">Только администраторы; участники — пока остаются на странице встречи</option>
                  <option value="participants">Администраторы и все участники этой встречи (из истории)</option>
                </select>
                <span className="help">Вариант «участники» оставляет им доступ к стенограмме и протоколам через «Историю». Отдельным людям доступ можно выдать на странице встречи.</span></label>
            </div>
          )}

          {tab === "features" && (
            <div role="tabpanel">
              <fieldset className="group"><legend>Что доступно участникам</legend>
                <div className="checks">
                  {([["camera_allowed", "Камера"], ["screen_share_allowed", "Демонстрация экрана"], ["board_allowed", "Общая доска"]] as const).map(([k, label]) => (
                    <label key={k} className="check"><input type="checkbox" checked={form[k]} onChange={(e) => set(k, e.target.checked)} /> {label}</label>
                  ))}
                </div>
                <span className="help">Руководители комнаты могут всё независимо от этих галочек. Транскрибация включена всегда: во время встречи руководитель может её приостановить.</span>
              </fieldset>
              <fieldset className="group"><legend>Тип комнаты и запись</legend>
                <label>Тип комнаты
                  <select value={form.room_type} onChange={(e) => set("room_type", e.target.value as RoomType)}>
                    <option value="regular">Обычная — все участники могут говорить</option>
                    <option value="presentation">Презентационная — участники слушают, говорят руководители и те, кому «дали слово»</option>
                  </select></label>
                <label className="check"><input type="checkbox" checked={form.record_audio || form.auto_record} disabled={form.auto_record} onChange={(e) => set("record_audio", e.target.checked)} />
                  <span className="check-body">Запись аудио разрешена<span className="help">Руководитель комнаты включает и выключает запись кнопкой во время встречи (хранение — в разделе «Хранилища»).</span></span></label>
                <label className="check"><input type="checkbox" checked={form.auto_record} onChange={(e) => set("auto_record", e.target.checked)} />
                  <span className="check-body">Начинать запись автоматически<span className="help">Запись начинается вместе со встречей; иначе её включают вручную.</span></span></label>
              </fieldset>
              <fieldset className="group"><legend>Гостевой доступ</legend>
                <label className="check"><input type="checkbox" checked={form.guest_access_enabled} onChange={(e) => set("guest_access_enabled", e.target.checked)} />
                  <span className="check-body">Разрешить гостевой доступ<span className="help">Выключено — войти могут только сотрудники из AD. Включено — по специальной ссылке можно войти без учётной записи: гость вводит имя, проверяет оборудование и попадает в уже идущую встречу как «Имя (гость)». Права гостя минимальны: без администрирования, истории, стенограммы и показа экрана. Выключение отключает гостей, но ссылка сохраняется.</span></span></label>
                <GuestLink form={form} onChange={(r) => setForm((f) => (f ? { ...f, guest_access_enabled: r.guest_access_enabled, guest_token: r.guest_token } : f))} />
              </fieldset>
              <fieldset className="group"><legend>Порядок на встрече</legend>
                <label className="check"><input type="checkbox" checked={form.mute_on_join} onChange={(e) => set("mute_on_join", e.target.checked)} />
                  <span className="check-body">Микрофон при входе выключен<span className="help">Участники заходят без звука и включают микрофон сами. Удобно для больших встреч и собраний: нет шума при подключении. Руководитель может выключить звук у всех одной кнопкой.</span></span></label>
              </fieldset>
            </div>
          )}

          {tab === "ai" && (
            <div role="tabpanel">
              <fieldset className="group"><legend>Обезличивание и модель</legend>
                <label>Обезличивание текста перед отправкой в языковую модель
                  <select value={form.anonymize_mode} onChange={(e) => set("anonymize_mode", e.target.value as AnonymizeMode)}>
                    <option value="inherit">Как в общих настройках (раздел «Обезличивание»)</option>
                    <option value="on">Всегда обезличивать (если сервис недоступен — протокол не создаётся)</option>
                    <option value="off">Не обезличивать — текст уходит в языковую модель как есть</option>
                  </select>
                  <span className="help">Выключение не мешает формированию протоколов и резюме: они создаются как обычно, но в модель уходит исходная стенограмма (с именами и данными участников). Выключайте, если языковая модель — внутренняя или данные в этой комнате не конфиденциальны.</span></label>
                <div className="cols">
                  <label>Языковая модель (LLM) для этой комнаты
                    <select value={form.llm_profile_id} onChange={(e) => set("llm_profile_id", e.target.value)}>
                      <option value="">По умолчанию (общая)</option>
                      {llmProfiles.filter((p) => !p.virtual).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <span className="help">Профили и выбор «по умолчанию» — в разделе «Языковая модель (LLM)».</span></label>
                  <label>Сервис обезличивания для этой комнаты
                    <select value={form.anonymizer_profile_id} onChange={(e) => set("anonymizer_profile_id", e.target.value)} disabled={form.anonymize_mode === "off"}>
                      <option value="">По умолчанию (общий)</option>
                      {anonProfiles.filter((p) => !p.virtual).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <span className="help">Профили — в разделе «Обезличивание».</span></label>
                </div>
              </fieldset>
              <label>Дополнение к инструкции протокола для этой комнаты <span className="muted small">(добавляется к общей инструкции в окне «Сформировать протокол»)</span>
                <textarea rows={3} value={form.protocol_instructions} onChange={(e) => set("protocol_instructions", e.target.value)} placeholder="Учитывать: протокол ведётся по форме отдела." />
                <span className="example">Пример: <code>Указывать номера задач из трекера, если они прозвучали.</code></span></label>
            </div>
          )}

          {tab === "storage" && (
            <div role="tabpanel">
              <fieldset className="group"><legend>Сроки хранения</legend>
                <div className="cols">
                  <label>Хранить текст <span className="muted small">(дней)</span><input type="number" min={0} value={form.text_retention_days} onChange={(e) => set("text_retention_days", e.target.value)} placeholder="бессрочно" />
                    <span className="help">Стенограммы и протоколы. Пусто — бессрочно; 0 — не хранить.</span><span className="example">Пример: <code>365</code></span></label>
                  <label>Хранить аудио <span className="muted small">(дней)</span><input type="number" min={0} value={form.audio_retention_days} onChange={(e) => set("audio_retention_days", e.target.value)} placeholder="бессрочно" />
                    <span className="help">Аудиозаписи. Пусто — бессрочно; 0 — удалять сразу после обработки.</span><span className="example">Пример: <code>30</code></span></label>
                </div>
              </fieldset>
            </div>
          )}

          <div className="row form-actions"><button className="btn primary">Сохранить</button><button type="button" className="btn ghost" onClick={() => setForm(null)}>Отмена</button></div>
        </form>
      )}

      <div className="table-scroll"><table className="table">
        <thead><tr><th>Название</th><th>Состояние</th><th>Доступ</th><th>Руководители</th><th>Опции</th><th /></tr></thead>
        <tbody>
          {rooms.map((r) => (
            <tr key={r.id}>
              <td>{r.name}{r.lifetime === "temporary" && <span className="badge"> временная</span>}<div className="muted small"><code>{r.slug}</code>{r.created_by_name ? ` · создал(а): ${r.created_by_name}` : ""}</div></td>
              <td>{r.lifecycle === "closed" ? <span className="badge">закрыта {r.closed_at ? new Date(r.closed_at).toLocaleString("ru-RU") : ""}</span> : r.lifecycle === "grace_period" ? <span className="badge warn">ждёт возврата</span> : r.is_enabled ? "включена" : <span className="badge warn">отключена</span>}{r.active_meeting_id && <span className="badge rec"> идёт встреча</span>}
                <div className="muted small">история: {r.history_access === "participants" ? "участникам" : "админам"}</div></td>
              <td className="small">{r.acl.length ? `${r.acl.length} запис.` : "только админы"}</td>
              <td className="small">{r.moderators?.length ? r.moderators.map(entryLabel).join(", ") : <span className="muted">—</span>}</td>
              <td className="small">{[r.room_type === "presentation" && "презентация", r.has_password && "пароль", r.auto_record ? "автозапись" : r.record_audio && "запись по кнопке", r.camera_allowed && "камера", r.screen_share_allowed && "экран", r.mute_on_join && "вход без звука", r.guest_access_enabled && "гости",
                r.anonymize_mode === "off" && "без обезличивания", r.anonymize_mode === "on" && "обезличивание всегда", (r.llm_profile_id || r.anonymizer_profile_id) && "свой API"].filter(Boolean).join(", ")}</td>
              <td className="actions"><button className="btn ghost" onClick={() => open(toForm(r))}>Изменить</button><button className="btn ghost danger" onClick={() => remove(r)}>Удалить</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}
