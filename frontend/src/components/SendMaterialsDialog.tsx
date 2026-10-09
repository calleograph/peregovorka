import { useEffect, useState } from "react";
import { api, type ApiError, type DeliveryPlan } from "../api";
import { Modal } from "./Dialogs";
import { FORMAT_LABEL, MATERIAL_KINDS, RecipientTable } from "./DeliveryEditor";

const EMAIL = /^[^\s@<>"',;]{1,64}@[^\s@<>"',;]+$/;

/**
 * «Отправить материалы» из истории встречи: получатели → документы и форматы → шаблон письма → предпросмотр темы и текста (их можно поправить только для этого письма,
 * шаблон не меняется) → «Отправить». Каждая отправка записывается в аудит и в журнал отправок встречи. Реквизиты почтового сервера здесь не показываются.
 */
export default function SendMaterialsDialog({ meetingId, onClose }: { meetingId: string; onClose: () => void }) {
  const [plan, setPlan] = useState<DeliveryPlan | null>(null);
  const [kinds, setKinds] = useState<string[]>([]);
  const [formats, setFormats] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [extra, setExtra] = useState("");
  const [tid, setTid] = useState<string>("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ queued: number; skipped: { name: string; email: string; reason: string }[]; unavailable: string[] } | null>(null);

  const applyTemplate = (p: DeliveryPlan, id: string) => {
    const t = p.templates?.find((x) => x.id === id) ?? p.templates?.[0];
    if (t) { setTid(t.id); setSubject(t.subject); setBody(t.body); }
  };

  useEffect(() => {
    api.delivery.preview(meetingId).then((p) => {
      setPlan(p);
      setKinds((p.selected ?? []).filter((k) => p.materials.find((m) => m.kind === k)?.available));
      setFormats(p.selected_formats ?? (p.attach_format ? [p.attach_format] : ["docx"]));
      setSelected(new Set(p.recipients.filter((r) => !r.problem && r.email).map((r) => r.email.toLowerCase())));
      applyTemplate(p, p.template_id ?? "");
    }).catch((e) => setError((e as ApiError).message));
  }, [meetingId]);

  const extraList = extra.split(/[\s,;]+/).filter(Boolean);
  const badExtra = extraList.filter((t) => !EMAIL.test(t));
  const uniq = Array.from(new Set([...selected, ...extraList.filter((t) => EMAIL.test(t)).map((t) => t.toLowerCase())]));
  const toggle = (e: string) => setSelected((s) => { const n = new Set(s); if (n.has(e)) n.delete(e); else n.add(e); return n; });
  const toggleKind = (k: string) => setKinds((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]));
  const toggleFmt = (f: string) => setFormats((c) => (c.includes(f) ? c.filter((x) => x !== f) : [...c, f]));
  const onlyLinks = kinds.length > 0 && kinds.every((k) => k === "map");

  const send = async () => {
    setBusy(true); setError("");
    try {
      const orig = new Map(plan?.recipients.map((r) => [r.email.toLowerCase(), r.email]) ?? []);
      setResult(await api.delivery.send(meetingId, kinds, uniq.map((e) => orig.get(e) ?? e), { template_id: tid || null, subject, body, formats }));
    } catch (e) { setError((e as ApiError).message); }
    setBusy(false);
  };

  return (
    <Modal title="Отправить материалы встречи" onClose={onClose} wide>
      {!plan && !error && <p className="muted">Загрузка…</p>}
      {error && <div className="alert error" role="alert">{error}</div>}
      {result ? (
        <div className="stack">
          <div className="alert ok" role="status">Поставлено в очередь писем: <b>{result.queued}</b>. Отправка идёт в фоне; результат — в блоке «Отправленные материалы» на странице встречи.</div>
          {result.unavailable.length > 0 && <div className="alert info">Не отправлено, т. к. ещё не сформировано: {result.unavailable.map((k) => MATERIAL_KINDS.find((m) => m.kind === k)?.label ?? k).join(", ")}.</div>}
          {result.skipped.length > 0 && <div className="alert info">Пропущены: {result.skipped.map((s) => `${s.name || s.email} — ${s.reason}`).join("; ")}.</div>}
          <div className="row"><button className="btn primary" onClick={onClose}>Закрыть</button></div>
        </div>
      ) : plan && (
        <div className="stack">
          {!plan.mail_configured && <div className="alert error">Исходящая почта не настроена. Обратитесь к администратору системы: отправка невозможна.</div>}
          <fieldset className="group"><legend>Кому ({uniq.length})</legend>
            <RecipientTable rows={plan.recipients} selected={selected} onToggle={toggle} />
            <label>Ещё адреса <span className="muted small">(необязательно)</span>
              <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="user1@example.local, partner@example.org" spellCheck={false} aria-invalid={badExtra.length > 0} />
              {badExtra.length > 0 && <span className="field-err" role="alert">Некорректный адрес: {badExtra[0]}</span>}</label>
            {plan.allowed_domains.length > 0 && <span className="help">Разрешены только адреса доменов: {plan.allowed_domains.join(", ")}.</span>}
          </fieldset>
          <fieldset className="group"><legend>Какие материалы</legend>
            {(plan.available_kinds ?? plan.materials).map((k) => {
              const m = plan.materials.find((x) => x.kind === k.kind);
              const avail = m ? m.available : undefined;
              return (
                <label key={k.kind} className="check"><input type="checkbox" checked={kinds.includes(k.kind)} disabled={avail === false} onChange={() => toggleKind(k.kind)} />
                  <span className="check-body">{k.label}{avail === false && <span className="badge warn">не готов</span>}{avail === false && m?.reason && <span className="help">{m.reason}</span>}
                    {k.describe && avail !== false && <span className="help">{k.describe}</span>}</span></label>);
            })}
          </fieldset>
          <fieldset className="group"><legend>Формат документов</legend>
            <div className="checks">{(plan.formats ?? ["docx", "pdf", "html", "txt", "md"]).map((f) => (
              <label key={f} className="check"><input type="checkbox" checked={formats.includes(f)} onChange={() => toggleFmt(f)} /> {FORMAT_LABEL[f] ?? f}</label>))}</div>
            <span className="help">Если документы вместе больше {plan.max_attachment_mb} МБ, они не вкладываются — в письме будет ссылка на страницу встречи.{onlyLinks ? " Карта разговора всегда уходит ссылкой." : ""}</span>
          </fieldset>
          <fieldset className="group"><legend>Письмо</legend>
            <label>Шаблон
              <select value={tid} onChange={(e) => applyTemplate(plan, e.target.value)}>
                {(plan.templates ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}{t.is_default ? " (по умолчанию)" : ""}</option>)}
              </select></label>
            <label>Тема<input value={subject} maxLength={300} onChange={(e) => setSubject(e.target.value)} /></label>
            <label>Текст письма<textarea rows={9} value={body} maxLength={8000} onChange={(e) => setBody(e.target.value)} /></label>
            <span className="help">Правки относятся только к этому письму — сам шаблон не меняется. Список материалов и ссылка на встречу добавляются под текстом автоматически.</span>
          </fieldset>
          <div className="row"><button className="btn primary" disabled={busy || !plan.mail_configured || kinds.length === 0 || uniq.length === 0 || badExtra.length > 0 || !subject.trim() || !body.trim() || (!onlyLinks && formats.length === 0)} onClick={() => void send()}>
            {busy ? "Отправка…" : `Отправить (${uniq.length})`}</button><button className="btn ghost" onClick={onClose}>Отмена</button></div>
        </div>
      )}
    </Modal>
  );
}
