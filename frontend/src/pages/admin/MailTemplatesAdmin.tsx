import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type MailTemplate } from "../../api";
import { useContextMenu } from "../../components/ContextMenu";

const empty = { name: "", subject: "", body: "", signature: "", materials: ["protocol", "summary"] as string[], is_default: false };

/** «Шаблоны писем»: тема, текст, подпись и материалы по умолчанию для рассылки материалов встреч; переменные {{...}} подставляются при отправке. */
export default function MailTemplatesAdmin() {
  const { onContextMenu, node: ctxNode } = useContextMenu();
  const [items, setItems] = useState<MailTemplate[]>([]);
  const [vars, setVars] = useState<{ name: string; describe: string }[]>([]);
  const [mats, setMats] = useState<{ kind: string; label: string }[]>([]);
  const [form, setForm] = useState<(typeof empty & { id?: string }) | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(async () => {
    try { const r = await api.mailTemplates.list(); setItems(r.items); setVars(r.variables); setMats(r.materials); } catch (e) { setError((e as ApiError).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    if (!form) return;
    setError(""); setNote("");
    try {
      if (form.id) await api.mailTemplates.update(form.id, form); else await api.mailTemplates.create(form);
      setForm(null); setNote("Шаблон сохранён."); await load();
    } catch (e) { setError((e as ApiError).message); }
  };
  const remove = async (t: MailTemplate) => {
    setError(""); setNote("");
    try { await api.mailTemplates.remove(t.id); setNote(`Шаблон «${t.name}» удалён.`); await load(); } catch (e) { setError((e as ApiError).message); }
  };
  const insert = (v: string) => setForm((f) => (f ? { ...f, body: `${f.body}{{${v}}}` } : f));

  return (
    <section>
      <div className="row"><h2>Шаблоны писем</h2><div className="spacer" /><button className="btn primary" onClick={() => setForm({ ...empty })}>＋ Новый шаблон</button></div>
      <p className="muted">Шаблон используется при отправке материалов встречи — вручную из истории и автоматически после встречи. Перед ручной отправкой тему и текст можно поправить только для этого письма: сам шаблон не изменится.
        Первоначальный шаблон создан для примера — измените его под свои правила.</p>
      {error && <div className="alert error" role="alert">{error}</div>}
      {note && <div className="alert ok" role="status">{note}</div>}
      {form && (
        <div className="card stack">
          <h3 style={{ margin: 0 }}>{form.id ? `Изменение шаблона «${form.name}»` : "Новый шаблон"}</h3>
          <label>Название<input value={form.name} maxLength={120} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Тема письма<input value={form.subject} maxLength={300} onChange={(e) => setForm({ ...form, subject: e.target.value })} placeholder="Материалы встречи «{{meeting_title}}» от {{meeting_date}}" /></label>
          <label>Текст письма<textarea rows={8} value={form.body} maxLength={8000} onChange={(e) => setForm({ ...form, body: e.target.value })} /></label>
          <label>Подпись<textarea rows={3} value={form.signature} maxLength={1000} onChange={(e) => setForm({ ...form, signature: e.target.value })} /></label>
          <div className="small"><b>Переменные</b> (нажмите, чтобы добавить в текст):
            <div className="chips">{vars.map((v) => <button key={v.name} type="button" className="chip" title={v.describe} onClick={() => insert(v.name)}>{`{{${v.name}}}`}</button>)}</div></div>
          <fieldset className="group"><legend>Какие материалы прикладывать по умолчанию</legend>
            {mats.map((m) => (
              <label key={m.kind} className="check"><input type="checkbox" checked={form.materials.includes(m.kind)}
                onChange={(e) => setForm({ ...form, materials: e.target.checked ? [...form.materials, m.kind] : form.materials.filter((x) => x !== m.kind) })} /> {m.label}</label>))}
          </fieldset>
          <label className="check"><input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} /> Шаблон по умолчанию</label>
          <div className="row"><button className="btn primary" onClick={() => void save()}>Сохранить</button><button className="btn ghost" onClick={() => setForm(null)}>Отмена</button></div>
        </div>
      )}
      {ctxNode}
      <div className="stack">
        {items.map((t) => (
          <div key={t.id} className="card" onContextMenu={onContextMenu(() => [
            { id: "edit", label: "Изменить", icon: "gear", onSelect: () => setForm({ ...t }) },
            { id: "dup", label: "Дублировать", icon: "copy", onSelect: () => setForm({ ...t, id: undefined, name: `${t.name} (копия)`, is_default: false } as typeof empty & { id?: string }) },
            { id: "def", label: "Сделать по умолчанию", icon: "sparkles", hidden: t.is_default, onSelect: () => void api.mailTemplates.update(t.id, { ...t, is_default: true }).then(load).catch((e) => setError((e as ApiError).message)) },
            { id: "del", label: "Удалить", icon: "close", danger: true, hidden: t.is_default, confirm: `Удалить шаблон «${t.name}»?`, onSelect: () => void remove(t) },
          ])}>
            <div className="row"><h3 style={{ margin: 0 }}>{t.name}</h3>{t.is_default && <span className="badge ok">по умолчанию</span>}<div className="spacer" />
              <button className="btn mini" onClick={() => setForm({ ...t })}>Изменить</button>
              {!t.is_default && <button className="btn mini ghost danger" onClick={() => void remove(t)}>Удалить</button>}</div>
            <div className="small"><b>Тема:</b> {t.subject}</div>
            <pre className="proto small" style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{t.body}{t.signature ? `\n\n${t.signature}` : ""}</pre>
          </div>))}
      </div>
    </section>
  );
}
