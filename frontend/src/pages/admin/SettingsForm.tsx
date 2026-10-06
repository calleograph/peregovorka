import { FormEvent, Fragment, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type SettingsGroup, type SettingsValues, type TestResult } from "../../api";

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

  const visible = fields.filter((f) => !f.showIf || f.showIf(values));
  const renderField = (f: Field) => (
    <label key={f.name} className={f.type === "bool" ? "check" : ""}>
      {f.type === "bool" ? (
        <>
          <input type="checkbox" checked={Boolean(values[f.name])} onChange={(e) => set(f.name, e.target.checked)} />
          <span className="check-body"><span>{f.label}</span>{f.help && <span className="help">{f.help}</span>}{f.example && <span className="example">Пример: <code>{f.example}</code></span>}</span>
        </>
      ) : (
        <>
          <span>{f.label}{f.unit && <span className="muted small"> ({f.unit})</span>}{f.type === "secret" && <span className="muted small"> — {values[`${f.name}_set`] ? "задан (оставьте пустым, чтобы не менять)" : "не задан"}</span>}</span>
          {f.type === "select" && <select value={String(values[f.name] ?? "")} onChange={(e) => set(f.name, e.target.value)}>{f.options?.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}
          {f.type === "textarea" && <textarea rows={f.rows ?? 5} value={String(values[f.name] ?? "")} placeholder={f.placeholder} onChange={(e) => set(f.name, e.target.value)} />}
          {f.type === "text" && <input value={String(values[f.name] ?? "")} placeholder={f.placeholder} onChange={(e) => set(f.name, e.target.value)} />}
          {f.type === "number" && (
            <input type="number" min={f.min} max={f.max} step={f.step} placeholder={f.nullable ? "не задано" : f.placeholder} style={{ maxWidth: 220 }}
                   value={values[f.name] === null || values[f.name] === undefined ? "" : Number(values[f.name])}
                   onChange={(e) => set(f.name, e.target.value === "" ? (f.nullable ? null : 0) : Number(e.target.value))} />
          )}
          {f.type === "secret" && (
            <div className="row">
              <input type="password" autoComplete="new-password" value={secrets[f.name] ?? ""} placeholder={values[`${f.name}_set`] ? "••••••••" : f.placeholder}
                     onChange={(e) => setSecrets({ ...secrets, [f.name]: e.target.value })} />
              {Boolean(values[`${f.name}_set`]) && <button type="button" className="btn mini" onClick={() => setSecrets({ ...secrets, [f.name]: "" })}>Очистить</button>}
            </div>
          )}
        </>
      )}
      {f.type !== "bool" && f.help && <span className="help">{f.help}</span>}
      {f.type !== "bool" && f.example && <span className="example">Пример: <code>{f.example}</code></span>}
    </label>
  );

  // подряд идущие поля одного раздела — в общую рамку с заголовком
  const blocks: { section?: string; items: Field[] }[] = [];
  for (const f of visible) {
    const last = blocks[blocks.length - 1];
    if (last && last.section === f.section) last.items.push(f); else blocks.push({ section: f.section, items: [f] });
  }

  return (
    <form className="card form" onSubmit={save}>
      <h2>{title}</h2>
      {intro && <p className="muted">{intro}</p>}
      {blocks.map((b, i) => (
        <Fragment key={`${b.section ?? "_"}-${i}`}>
          {b.section ? <fieldset className="group"><legend>{b.section}</legend>{b.items.map(renderField)}</fieldset> : b.items.map(renderField)}
        </Fragment>
      ))}
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
