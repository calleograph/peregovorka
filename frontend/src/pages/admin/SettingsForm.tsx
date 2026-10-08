import { FormEvent, useCallback, useEffect, useState } from "react";
import FieldsEditor from "./FieldsEditor";
import { api, type ApiError, type SettingsGroup, type SettingsValues, type StorageProfile, type TestResult } from "../../api";

export interface Field {
  name: string;
  /** Полное название поля — по нему администратор ориентируется в форме. */
  label: string;
  type: "text" | "number" | "bool" | "select" | "textarea" | "secret";
  options?: [string, string][];
  /** Что делает поле и на что влияет. */
  help?: string;
  /** Пример заполнения (показывается под полем). */
  example?: string;
  placeholder?: string;
  /** Единица измерения для числовых полей. */
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  rows?: number;
  /** Пустое значение = «не задано» (null), например бессрочный срок хранения. */
  nullable?: boolean;
  /** Смысловой раздел: подряд идущие поля одного раздела рамкой объединяются. */
  section?: string;
  showIf?: (v: SettingsValues) => boolean;
}

interface Props {
  group: SettingsGroup;
  title: string;
  intro?: string;
  fields: Field[];
  testable?: boolean;
  note?: string;
}

/** Универсальная форма настроек группы. Секреты никогда не приходят с сервера: показывается только «задан/не задан». */
export default function SettingsForm({ group, title, intro, fields, testable, note }: Props) {
  const [values, setValues] = useState<SettingsValues | null>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [busy, setBusy] = useState(false);

  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const wantsProfiles = fields.some((f) => f.name === "profile_id");
  useEffect(() => { if (wantsProfiles) void api.admin.storages().then((r) => setProfiles(r.items)).catch(() => undefined); }, [wantsProfiles]);
  const shown = wantsProfiles ? fields.map((f) => f.name !== "profile_id" ? f : { ...f, options: [["", profiles.length ? "Не выбрано" : "Не выбрано (хранилищ пока нет — создайте в разделе «Хранилища»)"] as [string, string],
    ...profiles.map((p) => [p.id, `${p.name} — ${p.address.length > 48 ? `${p.address.slice(0, 45)}…` : p.address}`] as [string, string])] }) : fields;

  const load = useCallback(() => api.admin.settings(group).then((v) => { setValues(v); setSecrets({}); }).catch((e) => setMsg({ ok: false, text: e.message })), [group]);
  useEffect(() => { setValues(null); setMsg(null); setTest(null); void load(); }, [load]);

  if (!values) return msg ? <div className="alert error">{msg.text}</div> : <div className="muted">Загрузка…</div>;
  const set = (name: string, v: string | number | boolean | null) => setValues({ ...values, [name]: v });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setMsg(null); setTest(null);
    const body: SettingsValues = {};
    for (const f of fields) {
      if (f.type === "secret") { if (f.name in secrets) body[f.name] = secrets[f.name]; }
      else body[f.name] = values[f.name] as string | number | boolean | null;
    }
    try { setValues(await api.admin.saveSettings(group, body)); setSecrets({}); setMsg({ ok: true, text: "Сохранено" }); }
    catch (err) { setMsg({ ok: false, text: (err as ApiError).message }); }
    finally { setBusy(false); }
  };

  const runTest = async () => {
    setBusy(true); setTest(null);
    try { setTest(await api.admin.testSettings(group)); } catch (err) { setTest({ ok: false, message: (err as ApiError).message, ms: 0 }); }
    finally { setBusy(false); }
  };

  return (
    <form className="card form" onSubmit={save}>
      <h2>{title}</h2>
      {intro && <p className="muted">{intro}</p>}
      <FieldsEditor fields={shown} values={values} onChange={set} secrets={secrets} onSecret={(n, v) => setSecrets((x) => ({ ...x, [n]: v }))} />
      {note && <p className="muted small">{note}</p>}
      <div className="row">
        <button className="btn primary" disabled={busy}>Сохранить</button>
        {testable && <button type="button" className="btn" onClick={runTest} disabled={busy}>Проверить подключение</button>}
      </div>
      {msg && <div className={`alert ${msg.ok ? "" : "error"}`} role="status">{msg.text}</div>}
      {test && <div className={`alert ${test.ok ? "ok" : "error"}`} role="status">{test.ok ? "✓ " : "✗ "}{test.message} <span className="muted small">({test.ms} мс)</span></div>}
    </form>
  );
}
