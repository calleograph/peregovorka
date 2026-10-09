import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ApiError, type LlmChoiceOnce, type ProtocolKind, type ProtocolPlan, type ProtocolTemplate } from "../api";
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
  const [plan, setPlan] = useState<ProtocolPlan | null>(null);
  const [choices, setChoices] = useState<LlmChoiceOnce[]>([]);
  const [canOverride, setCanOverride] = useState(false);
  const [llmKey, setLlmKey] = useState("");     // "" — модель по настройкам; иначе модель только для этого формирования

  const loadTemplates = useCallback(() => api.templates().then(setTemplates).catch(() => undefined), []);
  useEffect(() => { void loadTemplates(); }, [loadTemplates]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.defaultInstruction(meetingId, kind, llmKey).then((r) => {
      if (cancelled) return;
      setDefaultText(r.instruction);
      setPlan(r.plan ?? null);
      setChoices(r.llm_choices ?? []);
      setCanOverride(!!r.can_override);
      // смена модели пересчитывает только прогноз: введённая инструкция остаётся
      setInstruction((cur) => (cur.trim() ? cur : initialInstruction !== undefined && kind === kind0 ? initialInstruction : r.instruction));
    }).catch((e) => { if (!cancelled) setErr((e as ApiError).message); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId, kind, llmKey]);

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
    try { const r = await api.createProtocol(meetingId, kind, instruction.trim(), llmKey); onStarted(r.protocol_id); onClose(); }
    catch (e) { setErr((e as ApiError).message); setBusy(false); }
  };

  return (
    <Modal title="Сформировать протокол" onClose={onClose} wide>
      <div className="row">
        <span className="seg" role="group" aria-label="Вид документа">
          {(Object.keys(KIND_LABEL) as ProtocolKind[]).map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); setTplId(""); setInstruction(""); setLlmKey(""); }}>{KIND_LABEL[k]}</button>
          ))}
        </span>
      </div>
      {plan && (
        <fieldset className="group" aria-label="Какая модель будет работать">
          <legend>Модель</legend>
          <p style={{ margin: 0 }}>
            <b>{plan.llm_profile || "не выбрана"}</b>
            {plan.llm_local ? " · локальная, данные остаются на сервере" : plan.llm_ready ? ` · внешняя API${plan.llm_model ? `, модель ${plan.llm_model}` : ""}` : ""}
            {plan.once ? " · выбрана на один раз" : plan.llm_source === "meeting" ? " · настройка встречи" : plan.llm_source === "room" ? " · настройка переговорки" : plan.llm_source === "system" ? " · системная по умолчанию" : ""}
          </p>
          {canOverride && (
            <label style={{ marginTop: 6 }}>Использовать другую модель только для этого формирования
              <select value={llmKey} onChange={(e) => setLlmKey(e.target.value)} disabled={busy}>
                <option value="">Не менять: как настроено ({plan && !plan.once ? plan.llm_profile : "по настройкам"})</option>
                {choices.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
              <span className="help">Настройки переговорки и системы не меняются. Для внешней модели действует обезличивание по правилам переговорки; факт выбора записывается в журнал.</span>
            </label>
          )}
          {plan.forecast && (
            <p className="small" style={{ margin: "6px 0 0" }}>Ожидаемое время: <b>{plan.forecast.text}</b>
              <span className="muted"> · {plan.forecast.note}{plan.forecast.chunks > 1 ? ` · частей: ${plan.forecast.chunks}` : ""}</span></p>
          )}
          {plan.max_output_tokens ? (
            <p className="muted small" style={{ margin: "4px 0 0" }}>Предел длины ответа модели: {plan.max_output_tokens} токенов{plan.max_output_note ? ` (${plan.max_output_note})` : ""}.</p>
          ) : null}
        </fieldset>
      )}
      {plan?.warnings?.map((w) => <div key={w} className="alert info" role="status">⚠ {w}</div>)}
      <label>Инструкция для модели
        <textarea rows={9} value={instruction} onChange={(e) => setInstruction(e.target.value)} disabled={loading} maxLength={20000}
                  placeholder="Опишите, какой документ нужно получить" />
        <span className="help">Эту инструкцию вы подтверждаете перед отправкой. {plan?.llm_local
          ? "Текст обрабатывается встроенной локальной моделью на этом сервере и никуда не отправляется."
          : plan && !plan.anonymize
          ? "Обезличивание для этой переговорки выключено: стенограмма (с именами и данными участников) вместе с инструкцией уходит в языковую модель как есть. Ничего, кроме инструкции и стенограммы, не отправляется."
          : "Стенограмма сначала обезличивается (ФИО, контакты и т. п. заменяются метками), затем вместе с инструкцией уходит в языковую модель. Ничего, кроме инструкции и обезличенного текста, не отправляется."}
          {plan?.llm_profile ? ` Модель: ${plan.llm_profile}.` : ""}</span>
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
        <span className="muted small">Документ появится на странице встречи{plan?.forecast ? `; ожидайте ${plan.forecast.text}` : ""}.</span>
      </div>
    </Modal>
  );
}
