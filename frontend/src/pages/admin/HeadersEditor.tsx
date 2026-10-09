import type { SettingsValues } from "../../api";

/** Строка таблицы дополнительных заголовков. Секретный заголовок после сохранения не показывается: `keep` — значение уже задано на сервере и не менялось. */
export interface HeaderRow { name: string; value: string; secret: boolean; keep: boolean }

/** Строки из сохранённых значений: обычные заголовки с значениями, секретные — только имена. */
export function headersFromValues(values: SettingsValues): HeaderRow[] {
  const plain = (values.extra_headers ?? {}) as unknown as Record<string, string>;
  const names = (values.secret_header_names ?? []) as unknown as string[];
  return [
    ...Object.entries(plain).map(([name, value]) => ({ name, value: String(value), secret: false, keep: false })),
    ...names.map((name) => ({ name, value: "", secret: true, keep: true })),
  ];
}

/** Поля для отправки на сервер. Секретные значения: пустая строка у нетронутого — «оставить прежнее» (null), остальные — новое значение. */
export function headersPayload(rows: HeaderRow[]): { extra_headers: Record<string, string>; secret_headers: string } {
  const extra: Record<string, string> = {};
  const secret: Record<string, string | null> = {};
  for (const r of rows) {
    const name = r.name.trim();
    if (!name) continue;
    if (r.secret) secret[name] = r.keep && !r.value ? null : r.value;
    else extra[name] = r.value;
  }
  return { extra_headers: extra, secret_headers: JSON.stringify(secret) };
}

/** Редактор дополнительных заголовков запроса к API: имя, значение, «секретный» (значение шифруется и не показывается после сохранения). */
export default function HeadersEditor({ rows, onChange }: { rows: HeaderRow[]; onChange: (rows: HeaderRow[]) => void }) {
  const set = (i: number, patch: Partial<HeaderRow>) => onChange(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  return (
    <div className="headers-editor">
      {rows.length === 0 && <div className="muted small">Дополнительных заголовков нет.</div>}
      {rows.map((r, i) => (
        <div className="row" key={i} style={{ marginBottom: 6 }}>
          <input value={r.name} onChange={(e) => set(i, { name: e.target.value })} placeholder="Имя, например X-Org-Id" aria-label="Имя заголовка" spellCheck={false} style={{ maxWidth: 220 }} />
          <input type={r.secret ? "password" : "text"} autoComplete="off" value={r.value} spellCheck={false} aria-label="Значение заголовка"
                 onChange={(e) => set(i, { value: e.target.value, keep: false })} placeholder={r.secret && r.keep ? "•••••••• (задано; пусто — не менять)" : "Значение"} />
          <label className="check small" style={{ margin: 0 }}>
            <input type="checkbox" checked={r.secret} onChange={(e) => set(i, { secret: e.target.checked, keep: false })} /> секретный
          </label>
          <button type="button" className="btn mini ghost danger" onClick={() => onChange(rows.filter((_, k) => k !== i))} aria-label="Удалить заголовок">✕</button>
        </div>
      ))}
      <button type="button" className="btn mini" onClick={() => onChange([...rows, { name: "", value: "", secret: false, keep: false }])}>＋ Добавить заголовок</button>
      <div className="help">Секретные значения хранятся зашифрованно и обратно не показываются; служебные заголовки (Authorization, Content-Type) задаёт сам сервис.</div>
    </div>
  );
}
