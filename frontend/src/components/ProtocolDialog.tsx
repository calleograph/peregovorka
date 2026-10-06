import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ApiError, type ProtocolKind, type ProtocolTemplate } from "../api";
import { Modal } from "./Dialogs";

interface Props {
  meetingId: string;
  kind: ProtocolKind;
  isAdmin: boolean;
  /** Инструкция, с которой нужно начать (например, при повторном формировании); иначе — по умолчанию с сервера. */
  initialInstruction?: string;
  onClose: () => void;
  onStarted: (protocolId: string) => void;
}

const KIND_LABEL: Record<ProtocolKind, string> = { protocol: "Официальный протокол (полный)", summary: "Краткое резюме" };

/** Окно «Сформировать протокол»: инструкция для модели видна и редактируется ДО отправки; шаблоны — личные и общие. */
export default function ProtocolDialog({ meetingId, kind: kind0, isAdmin, initialInstruction, onClose, onStarted }: Props) {
  const [kind, setKind] = useState<ProtocolKind>(kind0);
  const [instruction, setInstruction] = useState(initialInstruction ?? "");
  const [defaultText, setDefaultText] = useState("");
  const [templates, setTemplates] = useState<ProtocolTemplate[]>([]);
  const [tplId, setTplId] = useState("");
  const [saveName, setSaveName] = useState("");
  const [saveGlobal, setSaveGlobal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);

  const loadTemplates = useCallback(() => api.templates().then(setTemplates).catch(() => undefined), []);
  useEffect(() => { void loadTemplates(); }, [loadTemplates]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.defaultInstruction(meetingId, kind).then((r) => {
      if (cancelled) return;
      setDefaultText(r.instruction);
      setInstruction((cur) => (initialInstruction !== undefined && kind === kind0 ? cur || initialInstruction : r.instruction));
    }).catch((e) => { if (!cancelled) setErr((e as ApiError).message); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId, kind]);

  const visible = useMemo(() => templates.filter((t) => t.kind === "any" || t.kind === kind), [templates, kind]);
  const chosen = templates.find((t) => t.id === tplId);

  const apply = () => { if (chosen) { setInstruction(chosen.instruction); setNote(`Применён шаблон «${chosen.name}»`); } };
  const saveAsTemplate = async () => {
    setErr(""); setNote("");
    try {
      const t = await api.createTemplate({ name: saveName.trim(), instruction, kind, scope: saveGlobal && isAdmin ? "global" : "user" });
      await loadTemplates(); setTplId(t.id); setSaveName(""); setNote(`Шаблон «${t.name}» сохранён`);
    } catch (e) { setErr((e as ApiError).message); }
  };
  const removeTemplate = async () => {
    if (!chosen) return;
    setErr("");
    try { await api.deleteTemplate(chosen.id); setTplId(""); await loadTemplates(); setNote("Шаблон удалён"); } catch (e) { setErr((e as ApiError).message); }
  };
  const start = async () => {
    setBusy(true); setErr("");
    try { const r = await api.createProtocol(meetingId, kind, instruction.trim()); onStarted(r.protocol_id); onClose(); }
    catch (e) { setErr((e as ApiError).message); setBusy(false); }
  };

  return (
    <Modal title="Сформировать протокол" onClose={onClose} wide>
      <div className="row">
        <span className="seg" role="group" aria-label="Вид документа">
          {(Object.keys(KIND_LABEL) as ProtocolKind[]).map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); setTplId(""); setInstruction(""); }}>{KIND_LABEL[k]}</button>
          ))}
        </span>
      </div>
      <label>Инструкция для модели
        <textarea rows={9} value={instruction} onChange={(e) => setInstruction(e.target.value)} disabled={loading} maxLength={20000}
                  placeholder="Опишите, какой документ нужно получить" />
        <span className="help">Эту инструкцию вы подтверждаете перед отправкой. Стенограмма сначала обезличивается (ФИО, контакты и т. п. заменяются метками), затем вместе с инструкцией уходит в языковую модель. Ничего, кроме инструкции и обезличенного текста, не отправляется.</span>
        <span className="example">Пример: <code>Составь протокол: тема, участники, решения, поручения (кто, что, срок). Ничего не выдумывай.</code></span>
      </label>
      <div className="row">
        <button type="button" className="btn mini" onClick={() => { setInstruction(defaultText); setNote("Восстановлена инструкция по умолчанию"); }} disabled={loading || !defaultText}>Инструкция по умолчанию</button>
        <span className="muted small">{instruction.length} / 20000</span>
      </div>

      <fieldset className="group">
        <legend>Шаблоны инструкций</legend>
        <div className="row">
          <select value={tplId} onChange={(e) => setTplId(e.target.value)} aria-label="Шаблон" style={{ minWidth: 240 }}>
            <option value="">— выберите шаблон —</option>
            {visible.map((t) => <option key={t.id} value={t.id}>{t.scope === "global" ? "Общий: " : "Мой: "}{t.name}</option>)}
          </select>
          <button type="button" className="btn mini" onClick={apply} disabled={!chosen}>Применить</button>
          {chosen?.can_edit && <button type="button" className="btn mini ghost danger" onClick={removeTemplate}>Удалить шаблон</button>}
        </div>
        <div className="row" style={{ margin: "10px 0" }}>
          <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="Название для сохранения текущей инструкции" style={{ maxWidth: 360 }} maxLength={200} />
          <button type="button" className="btn mini" onClick={saveAsTemplate} disabled={!saveName.trim() || !instruction.trim()}>Сохранить как шаблон</button>
          {isAdmin && <label className="check small" style={{ margin: 0 }}><input type="checkbox" checked={saveGlobal} onChange={(e) => setSaveGlobal(e.target.checked)} /> общий (для всех)</label>}
        </div>
      </fieldset>

      {err && <div className="alert error" role="alert">{err}</div>}
      {note && <div className="muted small" role="status">{note}</div>}
      <div className="row">
        <button className="btn primary" onClick={start} disabled={busy || loading || !instruction.trim()}>{busy ? "Отправка…" : "Сформировать"}</button>
        <button className="btn ghost" onClick={onClose} disabled={busy}>Отмена</button>
        <span className="muted small">Документ появится на странице встречи; обычно это занимает до минуты.</span>
      </div>
    </Modal>
  );
}
