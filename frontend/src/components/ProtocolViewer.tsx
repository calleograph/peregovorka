import { useEffect, useState } from "react";
import { api, type ApiError, type ExportFormat, type ProtocolItem } from "../api";
import { mdToPlain } from "../markdown";
import { copyText, fmt } from "../util";
import { ConfirmDialog } from "./Dialogs";
import { Markdown } from "./Markdown";
import Menu from "./Menu";

interface Props {
  meetingId: string;
  item: ProtocolItem;
  isAdmin: boolean;
  onChanged: (p: ProtocolItem) => void;
  onRegenerate: (p: ProtocolItem) => void;
  onDeleted: (id: string) => void;
}

const KIND_TITLE: Record<string, string> = { protocol: "Протокол", summary: "Краткое резюме" };
const FORMATS: [ExportFormat, string][] = [["docx", "Word (.docx)"], ["pdf", "PDF (.pdf)"], ["md", "Markdown (.md)"], ["txt", "Обычный текст (.txt)"]];

/** Просмотр протокола: отрисованный Markdown / исходник, правка вручную, копирование, скачивание, повторное формирование. */
export default function ProtocolViewer({ meetingId, item, isAdmin, onChanged, onRegenerate, onDeleted }: Props) {
  const [mode, setMode] = useState<"view" | "source" | "edit">("view");
  const [draft, setDraft] = useState(item.content ?? "");
  const [title, setTitle] = useState(item.title ?? "");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const content = item.content ?? "";

  useEffect(() => { setMode("view"); setDraft(item.content ?? ""); setTitle(item.title ?? ""); setMsg(null); }, [item.id]);
  useEffect(() => { if (mode !== "edit") setDraft(item.content ?? ""); }, [item.content, mode]);

  const flash = (ok: boolean, text: string) => { setMsg({ ok, text }); if (ok) window.setTimeout(() => setMsg((m) => (m?.text === text ? null : m)), 2500); };
  const copy = async (what: "plain" | "md") => flash(await copyText(what === "plain" ? mdToPlain(content) : content), what === "plain" ? "Скопировано как обычный текст" : "Скопировано как Markdown");

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const updated = await api.editProtocol(meetingId, item.id, { content: draft, title: title.trim() || undefined });
      onChanged({ ...item, ...updated, content: draft, instruction: item.instruction });
      setMode("view"); flash(true, "Изменения сохранены в составе встречи");
    } catch (e) { flash(false, (e as ApiError).message); }
    finally { setBusy(false); }
  };

  if (item.status === "pending") return <div className="card"><p className="muted" role="status">Документ формируется… Страница обновится сама.</p></div>;
  if (item.status === "failed") {
    return (
      <div className="card">
        <div className="alert error" role="alert">Не удалось сформировать: {item.error ?? "неизвестная ошибка"}</div>
        <div className="row"><button className="btn primary" onClick={() => onRegenerate(item)}>Сформировать заново</button>
          {isAdmin && <button className="btn ghost danger" onClick={() => setConfirmDelete(true)}>Удалить</button>}</div>
        {confirmDelete && <DeleteProtocol meetingId={meetingId} id={item.id} onDeleted={onDeleted} onClose={() => setConfirmDelete(false)} />}
      </div>
    );
  }

  return (
    <div className="card">
      <div className="row">
        <h2>{item.title || KIND_TITLE[item.kind] || "Документ"}</h2>
        <span className="badge">{KIND_TITLE[item.kind] ?? item.kind}</span>
        <div className="spacer" />
        <span className="seg" role="group" aria-label="Режим показа">
          <button aria-pressed={mode === "view"} onClick={() => setMode("view")}>Просмотр</button>
          <button aria-pressed={mode === "source"} onClick={() => setMode("source")}>Исходник</button>
        </span>
      </div>
      <p className="muted small">
        {fmt(item.created_at)}{item.created_by ? ` · сформировал: ${item.created_by}` : ""}{item.model ? ` · ${item.model}` : ""}
        {item.edited_at ? ` · правка: ${item.edited_by ?? ""} ${fmt(item.edited_at)}` : ""}
      </p>

      {item.warnings?.map((w) => (
        <div key={w} className={`alert ${item.truncated ? "error" : "info"}`} role="status">⚠ {w}</div>))}

      <div className="row" style={{ margin: "8px 0" }}>
        <span className="row tight" style={{ gap: 0 }}>
          <button className="btn" onClick={() => copy("plain")} title="Копировать как обычный текст — для писем и документов">Скопировать</button>
          <Menu label="" className="btn" title="Другие варианты копирования">
            <button onClick={() => copy("plain")}>Как обычный текст</button>
            <button onClick={() => copy("md")}>Как Markdown</button>
          </Menu>
        </span>
        <Menu label="Скачать">
          {FORMATS.map(([f, l]) => <a key={f} href={api.protocolExportUrl(meetingId, item.id, f)} download>{l}</a>)}
        </Menu>
        {mode !== "edit" && <button className="btn" onClick={() => { setDraft(content); setMode("edit"); }}>Редактировать</button>}
        <button className="btn" onClick={() => onRegenerate(item)} title="Открыть окно инструкции и создать новую версию">Сформировать заново</button>
        {isAdmin && <button className="btn ghost danger" onClick={() => setConfirmDelete(true)}>Удалить</button>}
      </div>
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}

      {mode === "view" && <Markdown source={content} />}
      {mode === "source" && <pre className="proto card" style={{ overflow: "auto" }}>{content}</pre>}
      {mode === "edit" && (
        <div>
          <label>Название<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} placeholder="Например: Протокол совещания по бюджету" /></label>
          <div className="row" style={{ alignItems: "stretch" }}>
            <label style={{ flex: "1 1 360px" }}>Текст (Markdown)
              <textarea className="source" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck />
            </label>
            <div style={{ flex: "1 1 360px", minWidth: 0 }}><div className="muted small" style={{ marginBottom: 4 }}>Предпросмотр</div><div className="card"><Markdown source={draft} /></div></div>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn primary" onClick={save} disabled={busy || !draft.trim()}>{busy ? "Сохранение…" : "Сохранить"}</button>
            <button className="btn ghost" onClick={() => { setDraft(content); setMode("view"); }} disabled={busy}>Отмена</button>
            <span className="muted small">Ручная правка сохраняется в составе встречи; автор и время правки фиксируются.</span>
          </div>
        </div>
      )}

      {item.instruction && (
        <details style={{ marginTop: 12 }}>
          <summary className="muted small">Инструкция, по которой создан документ</summary>
          <pre className="proto" style={{ marginTop: 6 }}>{item.instruction}</pre>
        </details>
      )}
      <p className="muted small">Если при создании было включено обезличивание, текст составлен по обезличенной стенограмме: метки вида [ФИО_1] — заменённые данные.</p>
      {confirmDelete && <DeleteProtocol meetingId={meetingId} id={item.id} onDeleted={onDeleted} onClose={() => setConfirmDelete(false)} />}
    </div>
  );
}

function DeleteProtocol({ meetingId, id, onDeleted, onClose }: { meetingId: string; id: string; onDeleted: (id: string) => void; onClose: () => void }) {
  return (
    <ConfirmDialog title="Удалить документ?" confirmLabel="Удалить" onClose={onClose}
      body={<p>Документ будет удалён из встречи (стенограмма и записи не затрагиваются). Действие попадёт в журнал аудита: кто, что и когда удалил.</p>}
      onConfirm={async () => { await api.deleteProtocol(meetingId, id); onDeleted(id); }} />
  );
}
