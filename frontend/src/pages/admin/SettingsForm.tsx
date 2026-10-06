import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type SettingsGroup, type SettingsValues, type TestResult } from "../../api";

export interface Field {
  name: string;
  label: string;
  type: "text" | "number" | "bool" | "select" | "textarea" | "secret";
  options?: [string, string][];
  help?: string;
  min?: number;
  max?: number;
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

  if (!values) return <div className="muted">Загрузка…</div>;
  const set = (name: string, v: string | number | boolean) => setValues({ ...values, [name]: v });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setMsg(null); setTest(null);
    const body: SettingsValues = {};
    for (const f of fields) {
      if (f.type === "secret") { if (f.name in secrets) body[f.name] = secrets[f.name]; }
      else body[f.name] = values[f.name] as string | number | boolean;
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
      {fields.filter((f) => !f.showIf || f.showIf(values)).map((f) => (
        <label key={f.name} className={f.type === "bool" ? "check" : ""}>
          {f.type === "bool" ? (
            <><input type="checkbox" checked={Boolean(values[f.name])} onChange={(e) => set(f.name, e.target.checked)} /> {f.label}</>
          ) : (
            <>
              <span>{f.label}{f.type === "secret" && <span className="muted small"> — {values[`${f.name}_set`] ? "задан (оставьте пустым, чтобы не менять)" : "не задан"}</span>}</span>
              {f.type === "select" && <select value={String(values[f.name] ?? "")} onChange={(e) => set(f.name, e.target.value)}>{f.options?.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}
              {f.type === "textarea" && <textarea rows={5} value={String(values[f.name] ?? "")} onChange={(e) => set(f.name, e.target.value)} />}
              {(f.type === "text") && <input value={String(values[f.name] ?? "")} onChange={(e) => set(f.name, e.target.value)} />}
              {f.type === "number" && <input type="number" min={f.min} max={f.max} value={Number(values[f.name] ?? 0)} onChange={(e) => set(f.name, Number(e.target.value))} />}
              {f.type === "secret" && (
                <div className="row">
                  <input type="password" autoComplete="new-password" value={secrets[f.name] ?? ""} placeholder={values[`${f.name}_set`] ? "••••••••" : ""}
                         onChange={(e) => setSecrets({ ...secrets, [f.name]: e.target.value })} />
                  {Boolean(values[`${f.name}_set`]) && <button type="button" className="btn mini" onClick={() => setSecrets({ ...secrets, [f.name]: "" })}>Очистить</button>}
                </div>
              )}
            </>
          )}
          {f.help && <span className="muted small">{f.help}</span>}
        </label>
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
