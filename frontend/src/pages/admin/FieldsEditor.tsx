import { Fragment } from "react";
import type { SettingsValues } from "../../api";
import type { Field } from "./SettingsForm";

/**
 * Редактор набора полей (название, пояснение, пример — у каждого). Используется и общей формой настроек, и профилями API.
 * Секреты не показываются: `values[name_set]` говорит, задан ли секрет; новое значение хранится отдельно в `secrets`.
 */
export default function FieldsEditor({ fields, values, onChange, secrets, onSecret }: {
  fields: Field[]; values: SettingsValues; onChange: (name: string, v: string | number | boolean | null) => void;
  secrets: Record<string, string>; onSecret: (name: string, v: string) => void;
}) {
  const set = onChange;
  const setSecrets = (next: Record<string, string>) => { for (const k of Object.keys(next)) if (next[k] !== secrets[k]) onSecret(k, next[k]); };
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
    <>
      {blocks.map((b, i) => (
        <Fragment key={`${b.section ?? "_"}-${i}`}>
          {b.section ? <fieldset className="group"><legend>{b.section}</legend>{b.items.map(renderField)}</fieldset> : b.items.map(renderField)}
        </Fragment>
      ))}
    </>
  );
}
