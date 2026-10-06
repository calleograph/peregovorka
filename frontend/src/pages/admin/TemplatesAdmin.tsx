import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type ProtocolTemplate } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";

interface Form { id?: string; name: string; kind: "any" | "protocol" | "summary"; instruction: string }
const empty: Form = { name: "", kind: "any", instruction: "" };
const KIND: Record<string, string> = { any: "любой документ", protocol: "полный протокол", summary: "краткое резюме" };

/** Общие шаблоны инструкций: появляются у всех пользователей в окне «Сформировать протокол». Личные шаблоны пользователи ведут сами. */
export default function TemplatesAdmin() {
  const [rows, setRows] = useState<ProtocolTemplate[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [err, setErr] = useState("");
  const [del, setDel] = useState<ProtocolTemplate | null>(null);
  const load = useCallback(() => api.templates().then((l) => setRows(l.filter((t) => t.scope === "global"))).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setErr("");
    try {
      if (form.id) await api.updateTemplate(form.id, { name: form.name.trim(), kind: form.kind, instruction: form.instruction.trim() });
      else await api.createTemplate({ name: form.name.trim(), kind: form.kind, instruction: form.instruction.trim(), scope: "global" });
      setForm(null); await load();
    } catch (x) { setErr((x as ApiError).message); }
  };

  return (
    <section>
      <h2>Общие шаблоны инструкций</h2>
      <p className="muted">Готовые инструкции для модели, которые видят все пользователи в окне «Сформировать протокол» (например, «Протокол по форме организации», «Только поручения»). Создание и изменение общих шаблонов записывается в аудит.</p>
      {err && <div className="alert error">{err}</div>}
      {!form && <button className="btn primary" onClick={() => setForm({ ...empty })}>Создать шаблон</button>}
      {form && (
        <form className="card form" onSubmit={save}>
          <h3>{form.id ? "Изменение шаблона" : "Новый шаблон"}</h3>
          <label>Название шаблона
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={200} placeholder="Протокол по форме организации" />
            <span className="example">Пример: <code>Только поручения с ответственными и сроками</code></span></label>
          <label>Для какого документа
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Form["kind"] })}>
              <option value="any">Любой (полный протокол и резюме)</option><option value="protocol">Только полный протокол</option><option value="summary">Только краткое резюме</option></select></label>
          <label>Инструкция для модели
            <textarea rows={7} value={form.instruction} onChange={(e) => setForm({ ...form, instruction: e.target.value })} required maxLength={20000} />
            <span className="help">Опишите структуру документа и требования к стилю. Стенограмма добавляется автоматически после обезличивания.</span>
            <span className="example">Пример: <code>Выдели только принятые решения и поручения. Для каждого поручения укажи ответственного и срок. Не выдумывай данные.</code></span></label>
          <div className="row"><button className="btn primary">Сохранить</button><button type="button" className="btn ghost" onClick={() => setForm(null)}>Отмена</button></div>
        </form>
      )}
      <table className="table"><thead><tr><th>Название</th><th>Для</th><th>Инструкция</th><th /></tr></thead><tbody>
        {rows.length === 0 && <tr><td colSpan={4} className="muted">Общих шаблонов пока нет.</td></tr>}
        {rows.map((t) => (
          <tr key={t.id}><td>{t.name}</td><td>{KIND[t.kind]}</td><td className="small">{t.instruction.length > 160 ? `${t.instruction.slice(0, 160)}…` : t.instruction}</td>
            <td className="actions"><button className="btn ghost" onClick={() => setForm({ id: t.id, name: t.name, kind: t.kind, instruction: t.instruction })}>Изменить</button>
              <button className="btn ghost danger" onClick={() => setDel(t)}>Удалить</button></td></tr>
        ))}
      </tbody></table>
      {del && <ConfirmDialog title="Удалить шаблон?" confirmLabel="Удалить" onClose={() => setDel(null)} body={<p>Шаблон «{del.name}» исчезнет у всех пользователей. Уже сформированные документы не меняются.</p>}
        onConfirm={async () => { await api.deleteTemplate(del.id); await load(); }} />}
    </section>
  );
}
