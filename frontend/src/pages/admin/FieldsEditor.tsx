import { Fragment, type ReactNode } from "react";
import type { SettingsValues } from "../../api";
import type { Field } from "./SettingsForm";
import HeadersEditor, { type HeaderRow } from "./HeadersEditor";
import InfoTip from "../../components/InfoTip";

/** Пояснение и пример поля — во всплывающей подсказке ⓘ (форма остаётся компактной; на телефоне подсказка открывается нажатием). */
function FieldTip({ f }: { f: Field }) {
  if (!f.help && !f.example) return null;
  return <InfoTip doc={f.doc}>{f.help}{f.example && <>{f.help ? " " : ""}Пример: <code>{f.example}</code></>}</InfoTip>;
}

/** Подпись поля: единица и значок ⓘ «приклеены» к последнему слову — переносятся вместе с ним, а не отдельной строкой. */
function Title({ text, tail }: { text: string; tail: ReactNode }) {
  const i = text.lastIndexOf(" ");
  return <>{i > 0 && text.slice(0, i + 1)}<span className="tip-tail">{i > 0 ? text.slice(i + 1) : text}{tail}</span></>;
}

/**
 * Редактор набора полей (название, пояснение, пример — у каждого). Используется и общей формой настроек, и профилями API.
 * Секреты не показываются: `values[name_set]` говорит, задан ли секрет; новое значение хранится отдельно в `secrets`.
 */
export default function FieldsEditor({ fields, values, onChange, secrets, onSecret, headers, onHeaders }: {
  fields: Field[]; values: SettingsValues; onChange: (name: string, v: string | number | boolean | null) => void;
  secrets: Record<string, string>; onSecret: (name: string, v: string) => void;
  /** Строки таблицы дополнительных заголовков (для полей типа «headers»); состояние держит форма-владелец. */
  headers?: HeaderRow[]; onHeaders?: (rows: HeaderRow[]) => void;
}) {
  const set = onChange;
  const setSecrets = (next: Record<string, string>) => { for (const k of Object.keys(next)) if (next[k] !== secrets[k]) onSecret(k, next[k]); };
  const visible = fields.filter((f) => !f.showIf || f.showIf(values));
  const renderField = (f: Field) => f.type === "headers" ? (
    <div key={f.name} id={`f-${f.name}`} className="field-block">
      <span className="field-title"><Title text={f.label} tail={<FieldTip f={f} />} /></span>
      <HeadersEditor rows={headers ?? []} onChange={(r) => onHeaders?.(r)} />
    </div>
  ) : (
    <label key={f.name} id={`f-${f.name}`} className={f.type === "bool" ? "check" : f.type === "select" && (f.options ?? []).some(([, l]) => l.length > 32) ? "wide" : ""}>
      {f.type === "bool" ? (
        <>
          <input type="checkbox" checked={Boolean(values[f.name])} onChange={(e) => set(f.name, e.target.checked)} />
          <span className="check-body"><span className="field-title"><Title text={f.label} tail={<FieldTip f={f} />} /></span></span>
        </>
      ) : (
        <>
          <span className="field-title"><Title text={f.label} tail={<>{f.unit && <span className="muted small"> ({f.unit})</span>}{f.type !== "secret" && <FieldTip f={f} />}</>} />{f.type === "secret" && <span className="muted small"> — {values[`${f.name}_set`] ? "задан (пусто — не менять)" : "не задан"}<FieldTip f={f} /></span>}</span>
          {f.type === "select" && <select value={String(values[f.name] ?? "")} onChange={(e) => set(f.name, e.target.value)}>{f.options?.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}
          {f.type === "textarea" && <textarea rows={f.rows ?? 5} value={String(values[f.name] ?? "")} placeholder={f.placeholder} onChange={(e) => set(f.name, e.target.value)} />}
          {f.type === "text" && <input value={String(values[f.name] ?? "")} placeholder={f.placeholder} onChange={(e) => set(f.name, e.target.value)} />}
          {f.type === "number" && (
            <input type="number" min={f.min} max={f.max} step={f.step} placeholder={f.nullable ? "не задано" : f.placeholder}
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
    </label>
  );

  // подряд идущие поля одного раздела — в общую рамку с заголовком; внутри — короткие поля и флажки сеткой, широкие — во всю ширину
  const blocks: { section?: string; items: Field[] }[] = [];
  for (const f of visible) {
    const last = blocks[blocks.length - 1];
    if (last && last.section === f.section) last.items.push(f); else blocks.push({ section: f.section, items: [f] });
  }
  const layout = (items: Field[]) => {
    const out: ReactNode[] = [];
    let run: Field[] = [], kind = "";
    const flush = () => {
      if (run.length) out.push(kind === "wide" ? run.map(renderField) : <div key={`g-${run[0].name}`} className={`field-grid ${kind === "bool" ? "checks" : ""}`}>{run.map(renderField)}</div>);
      run = [];
    };
    for (const f of items) {
      const k = f.type === "bool" ? "bool" : f.type === "textarea" || f.type === "headers" ? "wide" : "short";
      if (k !== kind) { flush(); kind = k; }
      run.push(f);
    }
    flush();
    return out;
  };
  return (
    <>
      {blocks.map((b, i) => (
        <Fragment key={`${b.section ?? "_"}-${i}`}>
          {b.section ? <fieldset className="group"><legend>{b.section}</legend>{layout(b.items)}</fieldset> : layout(b.items)}
        </Fragment>
      ))}
    </>
  );
}
