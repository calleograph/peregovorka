import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type ApiProfile, type ProfileKind, type SettingsValues, type TestResult } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";
import FieldsEditor from "./FieldsEditor";
import type { Field } from "./SettingsForm";

const SECRET: Record<ProfileKind, string> = { llm: "api_key", anonymizer: "token" };
const DEFAULTS: Record<ProfileKind, SettingsValues> = {
  llm: { type: "openai_compatible", base_url: "", model: "", routing_provider: "", max_tokens: 4000, temperature: 0.2, timeout: 180, use_corporate_ca: true, allow_http: false },
  anonymizer: { profile: "docclean", base_url: "", docclean_mode: "ai_ready", docclean_groups: "", connect_timeout: 5, timeout: 60, max_chunk_chars: 20000, use_corporate_ca: true,
    allow_http: false, endpoint: "/anonymize", request_field: "text", response_field: "anonymized_text", status_field: "", status_ok_value: "", auth_type: "bearer",
    auth_header_name: "X-API-Key", auth_username: "", extra_body: "" },
};
const WORDS: Record<ProfileKind, { one: string; many: string; where: string }> = {
  llm: { one: "языковой модели", many: "Несколько API языковой модели", where: "протоколов и резюме" },
  anonymizer: { one: "сервиса обезличивания", many: "Несколько API обезличивания", where: "обезличивания текста" },
};

interface Edit { id?: string; name: string; values: SettingsValues; secrets: Record<string, string>; secretSet: boolean }

/**
 * Профили API: можно завести несколько сервисов (например, внутренний и облачный), выбрать один «по умолчанию для всех» и при желании
 * назначить свой конкретной переговорке (в настройках комнаты). «Основной» профиль — прежние общие настройки на этой странице ниже.
 */
export default function ApiProfilesAdmin({ kind, fields }: { kind: ProfileKind; fields: Field[] }) {
  const [rows, setRows] = useState<ApiProfile[]>([]);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [tests, setTests] = useState<Record<string, TestResult | "running">>({});
  const [del, setDel] = useState<ApiProfile | null>(null);
  const w = WORDS[kind];
  const editFields = fields.filter((f) => f.name !== "enabled");

  const load = useCallback(() => api.admin.profiles(kind).then(setRows).catch((e) => setErr(e.message)), [kind]);
  useEffect(() => { setEdit(null); setTests({}); void load(); }, [load]);

  const open = (p?: ApiProfile) => setEdit(p
    ? { id: p.id, name: p.name, values: { ...DEFAULTS[kind], ...(p.config as SettingsValues), [`${SECRET[kind]}_set`]: p.secret_set }, secrets: {}, secretSet: p.secret_set }
    : { name: "", values: { ...DEFAULTS[kind], [`${SECRET[kind]}_set`]: false }, secrets: {}, secretSet: false });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    setBusy(true); setErr("");
    const config: Record<string, unknown> = {};
    for (const f of editFields) if (f.type !== "secret") config[f.name] = edit.values[f.name];
    const secret = SECRET[kind] in edit.secrets ? edit.secrets[SECRET[kind]] : undefined;
    try {
      if (edit.id) await api.admin.updateProfile(edit.id, { name: edit.name, config, ...(secret !== undefined ? { secret } : {}) });
      else await api.admin.createProfile({ kind, name: edit.name, config, secret: secret ?? "" });
      setEdit(null); await load();
    } catch (x) { setErr((x as ApiError).message); } finally { setBusy(false); }
  };

  const makeDefault = async (p: ApiProfile) => {
    setErr("");
    try { await api.admin.setDefaultProfile(kind, p.id); await load(); } catch (x) { setErr((x as ApiError).message); }
  };
  const test = async (p: ApiProfile) => {
    setTests((t) => ({ ...t, [p.id]: "running" }));
    try { const r = await api.admin.testProfile(kind, p.id); setTests((t) => ({ ...t, [p.id]: r })); }
    catch (x) { setTests((t) => ({ ...t, [p.id]: { ok: false, message: (x as ApiError).message, ms: 0 } })); }
  };

  const target = (p: ApiProfile) => {
    const c = p.config as Record<string, unknown>;
    return kind === "llm" ? `${c.model || "—"} · ${c.base_url || String(c.type)}` : `${c.profile === "generic" ? "произвольный API" : "DocClean"} · ${c.base_url || "—"}`;
  };

  return (
    <section className="card profiles">
      <div className="row"><h2>{w.many}</h2><div className="spacer" />
        {!edit && <button className="btn primary" onClick={() => open()}>Добавить API</button>}</div>
      <p className="muted">Можно подключить несколько API {w.one}: например, внутренний и внешний. Отметьте один «по умолчанию» — он будет использоваться для {w.where} во всех переговорках,
        у которых не выбран свой. Свой API назначается в настройках переговорки (Переговорки → Изменить → «Нейросети»). «Основной» — настройки этой страницы ниже.</p>
      {err && <div className="alert error" role="alert">{err}</div>}

      {!edit && (
        <table className="table">
          <thead><tr><th>По умолчанию</th><th>Название</th><th>Адрес и модель</th><th>Ключ</th><th /></tr></thead>
          <tbody>
            {rows.map((p) => {
              const t = tests[p.id];
              return (
                <tr key={p.id}>
                  <td><input type="radio" name={`default-${kind}`} checked={p.is_default} onChange={() => makeDefault(p)} aria-label={`Использовать «${p.name}» по умолчанию`} /></td>
                  <td>{p.name}{p.virtual && <span className="badge"> основной</span>}{p.is_default && <span className="badge ok"> по умолчанию</span>}</td>
                  <td className="small">{target(p)}</td>
                  <td className="small">{p.secret_set ? "задан" : <span className="muted">нет</span>}</td>
                  <td className="actions">
                    <button className="btn mini" onClick={() => test(p)} disabled={t === "running"}>{t === "running" ? "Проверка…" : "Проверить"}</button>{" "}
                    {!p.virtual && <><button className="btn mini" onClick={() => open(p)}>Изменить</button>{" "}<button className="btn mini ghost danger" onClick={() => setDel(p)}>Удалить</button></>}
                    {t && t !== "running" && <div className={`small ${t.ok ? "ok-text" : "field-err"}`}>{t.ok ? "✓ " : "✗ "}{t.message} ({t.ms} мс)</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {edit && (
        <form className="form" onSubmit={save}>
          <h3>{edit.id ? "Изменение API" : "Новый API"}</h3>
          <label>Название<input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} required maxLength={120}
                                placeholder={kind === "llm" ? "Внутренняя модель" : "DocClean резервный"} />
            <span className="help">Так профиль называется в списках и в настройках переговорок.</span></label>
          <FieldsEditor fields={editFields} values={edit.values} secrets={edit.secrets}
                        onChange={(n, v) => setEdit({ ...edit, values: { ...edit.values, [n]: v } })}
                        onSecret={(n, v) => setEdit({ ...edit, secrets: { ...edit.secrets, [n]: v } })} />
          <div className="row"><button className="btn primary" disabled={busy}>Сохранить</button><button type="button" className="btn ghost" onClick={() => setEdit(null)}>Отмена</button></div>
        </form>
      )}

      {del && (
        <ConfirmDialog title="Удалить профиль API?" confirmLabel="Удалить" onClose={() => setDel(null)}
          body={<p>Профиль «{del.name}» будет удалён. Переговорки, которые его использовали, перейдут на профиль по умолчанию. Действие записывается в журнал аудита.</p>}
          onConfirm={async () => { await api.admin.deleteProfile(del.id); await load(); }} />
      )}
    </section>
  );
}
