import { FormEvent, useCallback, useEffect, useState } from "react";
import FieldsEditor from "./FieldsEditor";
import InfoTip from "../../components/InfoTip";
import { useDirty } from "../../dirtyGuard";
import { headersFromValues, headersPayload, type HeaderRow } from "./HeadersEditor";
import { api, type ApiError, type SettingsGroup, type SettingsValues, type StorageProfile, type TestResult } from "../../api";

export interface Field {
  name: string;
  /** Полное название поля — по нему администратор ориентируется в форме. */
  label: string;
  type: "text" | "number" | "bool" | "select" | "textarea" | "secret" | "headers";
  options?: [string, string][];
  /** Что делает поле и на что влияет — показывается в подсказке ⓘ рядом с названием. */
  help?: string;
  /** Статья вики «Подробнее» для подсказки. */
  doc?: string;
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
  /** Вызывается после успешного сохранения (например, перечитать оформление сайта). */
  onSaved?: () => void;
}

/** Универсальная форма настроек группы. Секреты никогда не приходят с сервера: показывается только «задан/не задан». */
export default function SettingsForm({ group, title, intro, fields, testable, note, onSaved }: Props) {
  const [values, setValues] = useState<SettingsValues | null>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [headers, setHeaders] = useState<HeaderRow[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [base, setBase] = useState("");        // снимок сохранённого состояния: по нему видно, есть ли несохранённые правки

  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const wantsProfiles = fields.some((f) => f.name === "profile_id");
  useEffect(() => { if (wantsProfiles) void api.admin.storages().then((r) => setProfiles(r.items)).catch(() => undefined); }, [wantsProfiles]);
  const shown = wantsProfiles ? fields.map((f) => f.name !== "profile_id" ? f : { ...f, options: [["", profiles.length ? "Не выбрано" : "Не выбрано (хранилищ пока нет — создайте в разделе «Хранилища»)"] as [string, string],
    ...profiles.map((p) => [p.id, `${p.name} — ${p.address.length > 48 ? `${p.address.slice(0, 45)}…` : p.address}`] as [string, string])] }) : fields;

  const load = useCallback(() => api.admin.settings(group).then((v) => { setValues(v); setSecrets({}); const h = headersFromValues(v); setHeaders(h); setBase(JSON.stringify([v, h])); }).catch((e) => setMsg({ ok: false, text: e.message })), [group]);
  useEffect(() => { setValues(null); setMsg(null); setTest(null); void load(); }, [load]);

  const dirty = !!values && (base !== JSON.stringify([values, headers]) || Object.values(secrets).some((x) => x !== ""));
  useDirty(`settings:${group}`, dirty);

  if (!values) return msg ? <div className="alert error">{msg.text}</div> : <div className="muted">Загрузка…</div>;
  const set = (name: string, v: string | number | boolean | null) => setValues({ ...values, [name]: v });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setMsg(null); setTest(null);
    const body: SettingsValues = {};
    for (const f of fields) {
      if (f.type === "headers") Object.assign(body, headersPayload(headers) as unknown as SettingsValues);
      else if (f.type === "secret") { if (f.name in secrets) body[f.name] = secrets[f.name]; }
      else body[f.name] = values[f.name] as string | number | boolean | null;
    }
    try { const saved = await api.admin.saveSettings(group, body); setValues(saved); setSecrets({}); const h = headersFromValues(saved); setHeaders(h); setBase(JSON.stringify([saved, h])); setMsg({ ok: true, text: "Сохранено" }); onSaved?.(); }
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
      <h2 className="field-title">{title}{intro && intro.length > 140 && <InfoTip>{intro}</InfoTip>}</h2>
      {intro && intro.length <= 140 && <p className="muted small">{intro}</p>}
      <FieldsEditor fields={shown} values={values} onChange={set} secrets={secrets} onSecret={(n, v) => setSecrets((x) => ({ ...x, [n]: v }))} headers={headers} onHeaders={setHeaders} />
      {note && <p className="muted small">{note}</p>}
      <div className="row">
        <button className="btn primary" disabled={busy}>Сохранить</button>
        {dirty && <button type="button" className="btn ghost" onClick={() => { setMsg(null); setTest(null); void load(); }} disabled={busy}>Отменить изменения</button>}
        {dirty && <span className="badge warn" role="status">Есть несохранённые изменения</span>}
        {testable && <button type="button" className="btn" onClick={runTest} disabled={busy}>Проверить подключение</button>}
      </div>
      {msg && <div className={`alert ${msg.ok ? "" : "error"}`} role="status">{msg.text}</div>}
      {test && <div className={`alert ${test.ok ? "ok" : "error"}`} role="status">{test.ok ? "✓ " : "✗ "}{test.message} <span className="muted small">({test.ms} мс)</span></div>}
    </form>
  );
}
