import { useEffect, useState } from "react";
import { api, type ApiError, type UpdateChanges } from "../../api";
import { Modal } from "../../components/Dialogs";
import { MarkdownInline } from "../../components/Markdown";

const ALERT_TITLE: Record<string, string> = {
  db: "Схема базы данных", port: "Сетевые порты", restart: "Перезапуск LiveKit", manual: "Ручное действие", download: "Загрузка при обновлении", breaking: "Убирается прежнее поведение",
  config: "Конфигурация",
};

/** Что изменилось между установленной и доступной версиями (или в конкретном прошедшем обновлении): сначала важное, затем по смыслу. */
export function ChangesView({ data }: { data: UpdateChanges }) {
  const hasImportant = data.important.length > 0 || data.facts.length > 0;
  return (
    <div className="changes">
      <p style={{ margin: "0 0 8px" }}>
        Установлено: <b>{data.installed ?? "—"}</b> · Доступно: <b>{data.available ?? "—"}</b>
        {data.versions.length > 1 && <span className="muted small"> · версии: {data.versions.slice().reverse().join(", ")}</span>}
      </p>
      {hasImportant && (
        <div className="alert error" role="alert" style={{ marginBottom: 10 }}>
          <b>Обратите внимание перед обновлением</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
            {data.facts.map((f) => <li key={f.text}><b>{ALERT_TITLE[f.kind] ?? f.kind}:</b> {f.text}</li>)}
            {data.important.map((x, i) => (
              <li key={i}><b>{x.kinds.map((k) => ALERT_TITLE[k] ?? k).join(", ")}</b> · {x.version}: <MarkdownInline source={x.text} /></li>
            ))}
          </ul>
        </div>
      )}
      {data.empty && <p className="muted">В описании релиза изменений между этими версиями не найдено. Технический список — «Технический список» в разделе обновлений.</p>}
      {data.groups.map((g) => (
        <section key={g.id} style={{ marginBottom: 10 }}>
          <h4 style={{ margin: "8px 0 2px" }}>{g.title}</h4>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {g.items.map((it, i) => <li key={i}>{it.alerts.length > 0 && <span className="badge warn" title={it.alerts.map((k) => data.alert_text[k] ?? k).join(" ")}>важно </span>} <MarkdownInline source={it.text} /> <span className="muted small">({it.version})</span></li>)}
          </ul>
        </section>
      ))}
      <p className="muted small">Источник — CHANGELOG.md проекта (тот же текст, что в описании релиза на GitHub).</p>
    </div>
  );
}

/** Окно «Что нового»: текущие изменения (передаются готовыми) либо сохранённые для перехода версий из истории. */
export default function ChangesDialog({ data, from, to, onClose, footer }: { data?: UpdateChanges | null; from?: string; to?: string; onClose: () => void; footer?: React.ReactNode }) {
  const [saved, setSaved] = useState<UpdateChanges | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (data || !from || !to) return;
    void api.admin.updateChanges(from, to).then(setSaved).catch((e) => setErr((e as ApiError).message));
  }, [data, from, to]);
  const d = data ?? saved;
  return (
    <Modal title={data ? "Что нового в обновлении" : `Что изменилось: ${from} → ${to}`} onClose={onClose} wide>
      {err && <div className="alert info">{err}</div>}
      {!d && !err && <div className="muted">Загрузка…</div>}
      {d && <ChangesView data={d} />}
      <div className="row" style={{ marginTop: 10 }}>{footer}<button className="btn ghost" onClick={onClose}>Закрыть</button></div>
    </Modal>
  );
}
