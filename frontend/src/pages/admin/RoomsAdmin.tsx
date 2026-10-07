import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type AclEntry, type AnonymizeMode, type ApiError, type ApiProfile, type DirHit, type HistoryAccess, type RoomAdmin } from "../../api";

interface Form {
  id?: string; slug: string; name: string; description: string; is_enabled: boolean; max_participants: number;
  password: string; clearPassword: boolean; transcription_enabled: boolean; record_audio: boolean;
  camera_allowed: boolean; screen_share_allowed: boolean; text_retention_days: string; audio_retention_days: string;
  protocol_instructions: string; aclText: string; history_access: HistoryAccess;
  anonymize_mode: AnonymizeMode; llm_profile_id: string; anonymizer_profile_id: string;
}

const empty: Form = {
  slug: "", name: "", description: "", is_enabled: true, max_participants: 20, password: "", clearPassword: false,
  transcription_enabled: true, record_audio: false, camera_allowed: true, screen_share_allowed: true,
  text_retention_days: "", audio_retention_days: "", protocol_instructions: "", aclText: "", history_access: "admin",
  anonymize_mode: "inherit", llm_profile_id: "", anonymizer_profile_id: "",
};

// ACL в форме: по строке на запись — «group: <DN группы AD>» или «user: <objectGUID>».
const parseAcl = (t: string): AclEntry[] => t.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
  const m = /^(group|user)\s*:\s*(.+)$/i.exec(l);
  return m ? { subject_type: m[1].toLowerCase() as "group" | "user", subject_ref: m[2].trim() } : { subject_type: "group", subject_ref: l };
});
const aclToText = (acl: AclEntry[]) => acl.map((a) => `${a.subject_type}: ${a.subject_ref}`).join("\n");
const days = (v: string) => (v.trim() === "" ? null : Number(v));

function toForm(r: RoomAdmin): Form {
  return { id: r.id, slug: r.slug, name: r.name, description: r.description ?? "", is_enabled: r.is_enabled,
    max_participants: r.max_participants, password: "", clearPassword: false, transcription_enabled: r.transcription_enabled,
    record_audio: r.record_audio, camera_allowed: r.camera_allowed, screen_share_allowed: r.screen_share_allowed,
    text_retention_days: r.text_retention_days?.toString() ?? "", audio_retention_days: r.audio_retention_days?.toString() ?? "",
    protocol_instructions: r.protocol_instructions ?? "", aclText: aclToText(r.acl), history_access: r.history_access ?? "admin",
    anonymize_mode: r.anonymize_mode ?? "inherit", llm_profile_id: r.llm_profile_id ?? "", anonymizer_profile_id: r.anonymizer_profile_id ?? "" };
}

export default function RoomsAdmin() {
  const [rooms, setRooms] = useState<RoomAdmin[]>([]);
  const [form, setForm] = useState<Form | null>(null);
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

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setError(""); setNote("");
    const base = {
      name: form.name, description: form.description || null, is_enabled: form.is_enabled, max_participants: form.max_participants,
      transcription_enabled: form.transcription_enabled, record_audio: form.record_audio, camera_allowed: form.camera_allowed,
      screen_share_allowed: form.screen_share_allowed, text_retention_days: days(form.text_retention_days),
      audio_retention_days: days(form.audio_retention_days), protocol_instructions: form.protocol_instructions || null,
      acl: parseAcl(form.aclText), history_access: form.history_access,
      anonymize_mode: form.anonymize_mode, llm_profile_id: form.llm_profile_id || null, anonymizer_profile_id: form.anonymizer_profile_id || null,
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
  const search = async () => {
    setSearchMsg("");
    try { const r = await api.admin.search(kind, q); setHits(r); if (!r.length) setSearchMsg("Ничего не найдено"); }
    catch (e) { setHits([]); setSearchMsg((e as ApiError).message); }
  };
  const addHit = (h: DirHit) => setForm((f) => {
    if (!f) return f;
    const line = `${h.kind}: ${h.ref}`;
    const lines = f.aclText.split(/\r?\n/);
    return lines.includes(line) ? f : { ...f, aclText: [...lines.filter(Boolean), line].join(String.fromCharCode(10)) };
  });

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  return (
    <section>
      <h2>Переговорки</h2>
      {note && <div className="alert">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}
      {!form && <button className="btn primary" onClick={() => setForm({ ...empty })}>Создать комнату</button>}

      {form && (
        <form className="card form" onSubmit={save}>
          <h3>{form.id ? "Изменение комнаты" : "Новая комната"}</h3>
          <fieldset className="group"><legend>Основное</legend>
            <div className="cols">
              <label>Название<input value={form.name} onChange={(e) => set("name", e.target.value)} required placeholder="Переговорная «Север»" />
                <span className="example">Пример: <code>ИТ-1</code></span></label>
              <label>Технический идентификатор<input value={form.slug} onChange={(e) => set("slug", e.target.value)} disabled={!!form.id} required pattern="[a-z0-9][a-z0-9\-]{1,62}" placeholder="meeting-room-1" />
                <span className="help">Латиница, цифры и дефис; после создания не меняется.</span><span className="example">Пример: <code>it-1</code></span></label>
            </div>
            <label>Описание<input value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="Еженедельная планёрка отдела" /></label>
            <div className="cols">
              <label>Максимум участников<input type="number" min={1} max={200} value={form.max_participants} onChange={(e) => set("max_participants", Number(e.target.value))} />
                <span className="help">Больше людей войти не смогут.</span></label>
              <label>Пароль комнаты {form.id && <span className="muted small">(пусто — не менять)</span>}
                <input type="password" value={form.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" disabled={form.clearPassword} />
                <span className="help">Необязательно: участники вводят его при входе дополнительно к доменной учётной записи.</span></label>
              {form.id && <label className="check"><input type="checkbox" checked={form.clearPassword} onChange={(e) => set("clearPassword", e.target.checked)} /> Убрать пароль</label>}
            </div>
          </fieldset>
          <fieldset className="group"><legend>Возможности комнаты</legend>
            <div className="checks">
              {([["is_enabled", "Комната включена (доступна для входа)"], ["transcription_enabled", "Транскрибация"], ["record_audio", "Запись аудио"],
                 ["camera_allowed", "Камера"], ["screen_share_allowed", "Демонстрация экрана"]] as const).map(([k, label]) => (
                <label key={k} className="check"><input type="checkbox" checked={form[k]} onChange={(e) => set(k, e.target.checked)} /> {label}</label>
              ))}
            </div>
            <span className="help">Транскрибация формирует стенограмму; запись аудио сохраняет звук участников (хранение — в разделе «Хранилище записей»).</span>
          </fieldset>
          <fieldset className="group"><legend>Доступ к завершённым встречам</legend>
            <label>Кто видит встречу после её завершения
              <select value={form.history_access} onChange={(e) => set("history_access", e.target.value as HistoryAccess)}>
                <option value="admin">Только администраторы; участники — пока остаются на странице встречи</option>
                <option value="participants">Администраторы и все участники этой встречи (из истории)</option>
              </select>
              <span className="help">По умолчанию обычный участник после ухода со страницы встречи больше не видит стенограмму и протоколы. Вариант «участники» оставляет им доступ через «Историю». Отдельным людям доступ можно выдать на странице встречи (Администрирование → Доступ к встрече).</span></label>
          </fieldset>
          <fieldset className="group"><legend>Нейросети: протоколы, резюме и обезличивание</legend>
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
                <span className="help">Профили и выбор «по умолчанию» — в разделе «Языковая модель (LLM)». Пусто — используется общая.</span></label>
              <label>Сервис обезличивания для этой комнаты
                <select value={form.anonymizer_profile_id} onChange={(e) => set("anonymizer_profile_id", e.target.value)} disabled={form.anonymize_mode === "off"}>
                  <option value="">По умолчанию (общий)</option>
                  {anonProfiles.filter((p) => !p.virtual).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <span className="help">Профили — в разделе «Обезличивание».</span></label>
            </div>
          </fieldset>
          <fieldset className="group"><legend>Сроки хранения</legend>
            <div className="cols">
              <label>Хранить текст <span className="muted small">(дней)</span><input type="number" min={0} value={form.text_retention_days} onChange={(e) => set("text_retention_days", e.target.value)} placeholder="бессрочно" />
                <span className="help">Стенограммы и протоколы. Пусто — бессрочно; 0 — не хранить.</span><span className="example">Пример: <code>365</code></span></label>
              <label>Хранить аудио <span className="muted small">(дней)</span><input type="number" min={0} value={form.audio_retention_days} onChange={(e) => set("audio_retention_days", e.target.value)} placeholder="бессрочно" />
                <span className="help">Аудиозаписи. Пусто — бессрочно; 0 — удалять сразу после обработки.</span><span className="example">Пример: <code>30</code></span></label>
            </div>
          </fieldset>
          <fieldset className="group"><legend>Кто может заходить в комнату</legend>
            <label>Список доступа <span className="muted small">(по строке: «group: DN группы AD» или «user: objectGUID»; пусто — только администраторы)</span>
              <textarea rows={4} value={form.aclText} onChange={(e) => set("aclText", e.target.value)} placeholder="group: CN=Staff,OU=Groups,DC=corp,DC=local" />
              <span className="example">Пример: <code>group: CN=Отдел ИТ,OU=Группы,DC=corp,DC=local</code></span></label>
            <div className="picker">
              <div className="row">
                <select value={kind} onChange={(e) => setKind(e.target.value as "group" | "user")}><option value="group">Группа AD</option><option value="user">Пользователь AD</option></select>
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск в каталоге (от 2 символов), например: Отдел ИТ" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} />
                <button type="button" className="btn" onClick={search} disabled={q.trim().length < 2}>Найти</button>
              </div>
              {searchMsg && <div className="muted small">{searchMsg}</div>}
              {hits.map((h) => (
                <div key={h.ref} className="hit"><span>{h.name}{h.sam ? ` (${h.sam})` : ""} <span className="muted small">{h.kind === "group" ? h.ref : h.email}</span></span>
                  <button type="button" className="btn mini" onClick={() => addHit(h)}>Добавить</button></div>
              ))}
            </div>
          </fieldset>
          <label>Дополнение к инструкции протокола для этой комнаты <span className="muted small">(добавляется к общей инструкции в окне «Сформировать протокол»)</span>
            <textarea rows={3} value={form.protocol_instructions} onChange={(e) => set("protocol_instructions", e.target.value)} placeholder="Учитывать: протокол ведётся по форме отдела." />
            <span className="example">Пример: <code>Указывать номера задач из трекера, если они прозвучали.</code></span></label>
          <div className="row"><button className="btn primary">Сохранить</button><button type="button" className="btn ghost" onClick={() => setForm(null)}>Отмена</button></div>
        </form>
      )}

      <table className="table">
        <thead><tr><th>Название</th><th>Идентификатор</th><th>Состояние</th><th>Доступ</th><th>Опции</th><th /></tr></thead>
        <tbody>
          {rooms.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td><td><code>{r.slug}</code></td>
              <td>{r.is_enabled ? "включена" : "отключена"}{r.active_meeting_id && <span className="badge"> идёт встреча</span>}<div className="muted small">история: {r.history_access === "participants" ? "участникам" : "админам"}</div></td>
              <td>{r.acl.length ? `${r.acl.length} запис.` : "только админы"}</td>
              <td className="small">{[r.has_password && "пароль", r.transcription_enabled && "текст", r.record_audio && "аудио", r.camera_allowed && "камера", r.screen_share_allowed && "экран",
                r.anonymize_mode === "off" && "без обезличивания", r.anonymize_mode === "on" && "обезличивание всегда", (r.llm_profile_id || r.anonymizer_profile_id) && "свой API"].filter(Boolean).join(", ")}</td>
              <td className="actions"><button className="btn ghost" onClick={() => setForm(toForm(r))}>Изменить</button><button className="btn ghost danger" onClick={() => remove(r)}>Удалить</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
