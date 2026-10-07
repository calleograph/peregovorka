import { FormEvent, useCallback, useEffect, useState } from "react";
import { copyText } from "../../util";
import { api, type AclEntry, type AnonymizeMode, type ApiError, type ApiProfile, type DirHit, type HistoryAccess, type RoomAdmin } from "../../api";

interface Form {
  id?: string; slug: string; name: string; description: string; is_enabled: boolean; max_participants: number;
  password: string; clearPassword: boolean; transcription_enabled: boolean; record_audio: boolean;
  camera_allowed: boolean; screen_share_allowed: boolean; text_retention_days: string; audio_retention_days: string;
  protocol_instructions: string; acl: AclEntry[]; moderators: AclEntry[]; history_access: HistoryAccess;
  anonymize_mode: AnonymizeMode; llm_profile_id: string; anonymizer_profile_id: string; mute_on_join: boolean; welcome_message: string;
  guest_access_enabled: boolean; guest_token: string | null;
}

const empty: Form = {
  slug: "", name: "", description: "", is_enabled: true, max_participants: 20, password: "", clearPassword: false,
  transcription_enabled: true, record_audio: false, camera_allowed: true, screen_share_allowed: true,
  text_retention_days: "", audio_retention_days: "", protocol_instructions: "", acl: [], moderators: [], history_access: "admin",
  anonymize_mode: "inherit", llm_profile_id: "", anonymizer_profile_id: "", mute_on_join: false, welcome_message: "",
  guest_access_enabled: false, guest_token: null,
};

const days = (v: string) => (v.trim() === "" ? null : Number(v));
const SLUG = /^[a-z0-9][a-z0-9-]{1,62}$/;

function toForm(r: RoomAdmin): Form {
  return { id: r.id, slug: r.slug, name: r.name, description: r.description ?? "", is_enabled: r.is_enabled,
    max_participants: r.max_participants, password: "", clearPassword: false, transcription_enabled: r.transcription_enabled,
    record_audio: r.record_audio, camera_allowed: r.camera_allowed, screen_share_allowed: r.screen_share_allowed,
    text_retention_days: r.text_retention_days?.toString() ?? "", audio_retention_days: r.audio_retention_days?.toString() ?? "",
    protocol_instructions: r.protocol_instructions ?? "", acl: r.acl, moderators: r.moderators ?? [], history_access: r.history_access ?? "admin",
    anonymize_mode: r.anonymize_mode ?? "inherit", llm_profile_id: r.llm_profile_id ?? "", anonymizer_profile_id: r.anonymizer_profile_id ?? "",
    mute_on_join: r.mute_on_join ?? false, welcome_message: r.welcome_message ?? "",
    guest_access_enabled: r.guest_access_enabled ?? false, guest_token: r.guest_token ?? null };
}

type TabId = "main" | "access" | "features" | "ai" | "storage";
const TABS: [TabId, string][] = [["main", "Основное"], ["access", "Доступ и руководители"], ["features", "Возможности"], ["ai", "Нейросети и протоколы"], ["storage", "Хранение"]];

const entryLabel = (e: AclEntry) => e.display_name || (e.subject_type === "group" ? e.subject_ref.split(",")[0].replace(/^cn=/i, "") : e.subject_ref);

/** Список «групп и людей» с кнопкой удаления у каждого: доступ и руководители редактируются одинаково. */
function Chips({ items, onRemove, empty: emptyText }: { items: AclEntry[]; onRemove: (i: number) => void; empty: string }) {
  if (!items.length) return <div className="muted small">{emptyText}</div>;
  return (
    <ul className="chips-list">
      {items.map((e, i) => (
        <li key={`${e.subject_type}:${e.subject_ref}`} title={e.subject_ref}>
          <span className="chip-kind">{e.subject_type === "group" ? "группа" : "человек"}</span> {entryLabel(e)}
          <button type="button" className="chip-x" onClick={() => onRemove(i)} aria-label={`Убрать ${entryLabel(e)}`}>✕</button>
        </li>
      ))}
    </ul>
  );
}

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
  useEffect(() => {
    if (!form) return;
    void api.admin.profiles("llm").then(setLlmProfiles).catch(() => undefined);
    void api.admin.profiles("anonymizer").then(setAnonProfiles).catch(() => undefined);
  }, [form === null]);  // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(() => api.admin.rooms().then(setRooms).catch((e) => setError(e.message)), []);
  useEffect(() => { void load(); }, [load]);

  const open = (f: Form) => { setForm(f); setTab("main"); setError(""); };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setError(""); setNote("");
    if (!form.name.trim()) { setTab("main"); setError("Укажите название комнаты."); return; }
    if (!form.id && !SLUG.test(form.slug)) { setTab("main"); setError("Технический идентификатор: латиница, цифры и дефис, 2–63 символа, начинается с буквы или цифры."); return; }
    const base = {
      name: form.name, description: form.description || null, is_enabled: form.is_enabled, max_participants: form.max_participants,
      transcription_enabled: form.transcription_enabled, record_audio: form.record_audio, camera_allowed: form.camera_allowed,
      screen_share_allowed: form.screen_share_allowed, text_retention_days: days(form.text_retention_days),
      audio_retention_days: days(form.audio_retention_days), protocol_instructions: form.protocol_instructions || null,
      acl: form.acl, moderators: form.moderators, history_access: form.history_access,
      anonymize_mode: form.anonymize_mode, llm_profile_id: form.llm_profile_id || null, anonymizer_profile_id: form.anonymizer_profile_id || null,
      mute_on_join: form.mute_on_join, welcome_message: form.welcome_message.trim() || null,
      guest_access_enabled: form.guest_access_enabled,
    };
    try {
      if (form.id) {
        await api.admin.patchRoom(form.id, { ...base, ...(form.clearPassword ? { password: "" } : form.password ? { password: form.password } : {}) });
      } else {
        await api.admin.createRoom({ ...base, slug: form.slug, password: form.password || null });
      }
      setForm(null); setNote("Сохранено"); await load();
    } catch (err) { setError((err as ApiError).message); }
  };

  const remove = async (r: RoomAdmin) => {
    if (!window.confirm(`Удалить комнату «${r.name}» вместе с историей встреч?`)) return;
    try { await api.admin.deleteRoom(r.id); await load(); } catch (err) { setError((err as ApiError).message); }
  };

  const [hits, setHits] = useState<DirHit[]>([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"group" | "user">("group");
  const [searchMsg, setSearchMsg] = useState("");
  const [manual, setManual] = useState("");
  const search = async () => {
    setSearchMsg("");
    try { const r = await api.admin.search(kind, q); setHits(r); if (!r.length) setSearchMsg("Ничего не найдено"); }
    catch (e) { setHits([]); setSearchMsg((e as ApiError).message); }
  };
  const addTo = (list: "acl" | "moderators", entry: AclEntry) => setForm((f) => {
    if (!f) return f;
    const ref = entry.subject_ref.trim().toLowerCase();
    if (f[list].some((x) => x.subject_type === entry.subject_type && x.subject_ref.toLowerCase() === ref)) return f;
    return { ...f, [list]: [...f[list], { ...entry, subject_ref: entry.subject_ref.trim() }] };
  });
  const hitEntry = (h: DirHit): AclEntry => ({ subject_type: h.kind, subject_ref: h.ref, display_name: h.name });
  const addManual = (list: "acl" | "moderators") => {
    const ref = manual.trim();
    if (!ref) return;
    addTo(list, { subject_type: /^cn=/i.test(ref) ? "group" : kind, subject_ref: ref });
    setManual("");
  };
  const removeFrom = (list: "acl" | "moderators", i: number) => setForm((f) => (f ? { ...f, [list]: f[list].filter((_, k) => k !== i) } : f));

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  return (
    <section>
      <div className="row"><h2>Переговорки</h2><div className="spacer" />
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
                <label>Технический идентификатор<input value={form.slug} onChange={(e) => set("slug", e.target.value)} disabled={!!form.id} placeholder="meeting-room-1" />
                  <span className="help">Латиница, цифры и дефис; после создания не меняется.</span><span className="example">Пример: <code>it-1</code></span></label>
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
              <div className="cols two">
                <div>
                  <h4>Кто может заходить</h4>
                  <p className="help">Группы и люди из каталога. Если список пуст — заходят только администраторы сервера.</p>
                  <Chips items={form.acl} empty="Пока никого — вход только у администраторов." onRemove={(i) => removeFrom("acl", i)} />
                </div>
                <div>
                  <h4>Руководители комнаты</h4>
                  <p className="help">Могут выключать микрофоны у всех участников или у одного (например, при постоянном шуме). Вход в комнату им разрешён всегда. Администраторы сервера — руководители всех комнат автоматически.</p>
                  <Chips items={form.moderators} empty="Руководители не назначены (микрофоны могут выключать только администраторы сервера)." onRemove={(i) => removeFrom("moderators", i)} />
                </div>
              </div>
              <div className="picker">
                <div className="row">
                  <select value={kind} onChange={(e) => setKind(e.target.value as "group" | "user")} aria-label="Что искать"><option value="group">Группа AD</option><option value="user">Пользователь AD</option></select>
                  <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск в каталоге (от 2 символов), например: Отдел ИТ" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} />
                  <button type="button" className="btn" onClick={search} disabled={q.trim().length < 2}>Найти</button>
                </div>
                {searchMsg && <div className="muted small">{searchMsg}</div>}
                {hits.map((h) => (
                  <div key={h.ref} className="hit"><span>{h.name}{h.sam ? ` (${h.sam})` : ""} <span className="muted small">{h.kind === "group" ? h.ref : h.email}</span></span>
                    <span className="row tight"><button type="button" className="btn mini" onClick={() => addTo("acl", hitEntry(h))}>＋ В доступ</button>
                      <button type="button" className="btn mini primary" onClick={() => addTo("moderators", hitEntry(h))}>＋ Руководителем</button></span></div>
                ))}
                <div className="row">
                  <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="Или вручную: DN группы (CN=…) либо objectGUID пользователя" aria-label="Добавить вручную" />
                  <button type="button" className="btn mini" disabled={!manual.trim()} onClick={() => addManual("acl")}>＋ В доступ</button>
                  <button type="button" className="btn mini" disabled={!manual.trim()} onClick={() => addManual("moderators")}>＋ Руководителем</button>
                </div>
                <span className="example">Пример группы: <code>CN=Отдел ИТ,OU=Группы,DC=corp,DC=local</code></span>
              </div>
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
                  {([["transcription_enabled", "Транскрибация"], ["record_audio", "Запись аудио"], ["camera_allowed", "Камера"], ["screen_share_allowed", "Демонстрация экрана"]] as const).map(([k, label]) => (
                    <label key={k} className="check"><input type="checkbox" checked={form[k]} onChange={(e) => set(k, e.target.checked)} /> {label}</label>
                  ))}
                </div>
                <span className="help">Транскрибация формирует стенограмму; запись аудио сохраняет звук участников (хранение — в разделе «Хранилище записей»).</span>
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
              <td>{r.name}<div className="muted small"><code>{r.slug}</code></div></td>
              <td>{r.is_enabled ? "включена" : <span className="badge warn">отключена</span>}{r.active_meeting_id && <span className="badge rec"> идёт встреча</span>}
                <div className="muted small">история: {r.history_access === "participants" ? "участникам" : "админам"}</div></td>
              <td className="small">{r.acl.length ? `${r.acl.length} запис.` : "только админы"}</td>
              <td className="small">{r.moderators?.length ? r.moderators.map(entryLabel).join(", ") : <span className="muted">—</span>}</td>
              <td className="small">{[r.has_password && "пароль", r.transcription_enabled && "текст", r.record_audio && "аудио", r.camera_allowed && "камера", r.screen_share_allowed && "экран", r.mute_on_join && "вход без звука", r.guest_access_enabled && "гости",
                r.anonymize_mode === "off" && "без обезличивания", r.anonymize_mode === "on" && "обезличивание всегда", (r.llm_profile_id || r.anonymizer_profile_id) && "свой API"].filter(Boolean).join(", ")}</td>
              <td className="actions"><button className="btn ghost" onClick={() => open(toForm(r))}>Изменить</button><button className="btn ghost danger" onClick={() => remove(r)}>Удалить</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}
