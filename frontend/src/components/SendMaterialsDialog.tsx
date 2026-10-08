import { useEffect, useState } from "react";
import { api, type ApiError, type DeliveryPlan } from "../api";
import { Modal } from "./Dialogs";
import { MATERIAL_KINDS, RecipientTable } from "./DeliveryEditor";

const EMAIL = /^[^\s@<>"',;]{1,64}@[^\s@<>"',;]+$/;

/**
 * «Отправить материалы»: сначала экран предварительного просмотра — какие документы и каким адресам уйдут, — затем подтверждение.
 * Повторная отправка допустима; каждое действие записывается в аудит. Реквизиты почтового сервера здесь не показываются.
 */
export default function SendMaterialsDialog({ meetingId, onClose }: { meetingId: string; onClose: () => void }) {
  const [plan, setPlan] = useState<DeliveryPlan | null>(null);
  const [kinds, setKinds] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [extra, setExtra] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ queued: number; skipped: { name: string; email: string; reason: string }[]; unavailable: string[] } | null>(null);

  useEffect(() => {
    api.delivery.preview(meetingId).then((p) => {
      setPlan(p);
      setKinds((p.selected ?? []).filter((k) => p.materials.find((m) => m.kind === k)?.available));
      setSelected(new Set(p.recipients.filter((r) => !r.problem && r.email).map((r) => r.email.toLowerCase())));
    }).catch((e) => setError((e as ApiError).message));
  }, [meetingId]);

  const extraList = extra.split(/[\s,;]+/).filter(Boolean);
  const badExtra = extraList.filter((t) => !EMAIL.test(t));
  const emails = [...selected, ...extraList.filter((t) => EMAIL.test(t)).map((t) => t.toLowerCase())];
  const uniq = Array.from(new Set(emails));
  const toggle = (e: string) => setSelected((s) => { const n = new Set(s); if (n.has(e)) n.delete(e); else n.add(e); return n; });
  const toggleKind = (k: string) => setKinds((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]));

  const send = async () => {
    setBusy(true); setError("");
    try {
      // исходный регистр адреса сохраняем из плана; свои адреса отправляем как введены
      const orig = new Map(plan?.recipients.map((r) => [r.email.toLowerCase(), r.email]) ?? []);
      setResult(await api.delivery.send(meetingId, kinds, uniq.map((e) => orig.get(e) ?? e)));
    } catch (e) { setError((e as ApiError).message); }
    setBusy(false);
  };

  return (
    <Modal title="Отправить материалы встречи" onClose={onClose} wide>
      {!plan && !error && <p className="muted">Загрузка…</p>}
      {error && <div className="alert error" role="alert">{error}</div>}
      {result ? (
        <div className="stack">
          <div className="alert ok" role="status">Поставлено в очередь писем: <b>{result.queued}</b>. Отправка идёт в фоне; результат — в журнале отправки у администратора.</div>
          {result.unavailable.length > 0 && <div className="alert info">Не отправлено, т. к. ещё не сформировано: {result.unavailable.map((k) => MATERIAL_KINDS.find((m) => m.kind === k)?.label ?? k).join(", ")}.</div>}
          {result.skipped.length > 0 && <div className="alert info">Пропущены: {result.skipped.map((s) => `${s.name || s.email} — ${s.reason}`).join("; ")}.</div>}
          <div className="row"><button className="btn primary" onClick={onClose}>Закрыть</button></div>
        </div>
      ) : plan && (
        <div className="stack">
          {!plan.mail_configured && <div className="alert error">Исходящая почта не настроена. Обратитесь к администратору системы: отправка невозможна.</div>}
          <fieldset className="group"><legend>Какие документы</legend>
            {plan.materials.map((m) => (
              <label key={m.kind} className="check"><input type="checkbox" checked={kinds.includes(m.kind)} disabled={!m.available} onChange={() => toggleKind(m.kind)} />
                <span className="check-body">{m.label}{!m.available && <span className="badge warn">не готов</span>}{m.reason && !m.available && <span className="help">{m.reason}</span>}</span></label>))}
            {(plan.available_kinds ?? []).filter((k) => !plan.materials.some((m) => m.kind === k.kind)).length > 0 && <span className="help">Другие материалы можно включить в настройках комнаты.</span>}
          </fieldset>
          <fieldset className="group"><legend>Кому ({uniq.length})</legend>
            <RecipientTable rows={plan.recipients} selected={selected} onToggle={toggle} />
            <label>Ещё адреса <span className="muted small">(необязательно)</span>
              <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="user1@example.local, partner@example.org" spellCheck={false} aria-invalid={badExtra.length > 0} />
              {badExtra.length > 0 && <span className="field-err" role="alert">Некорректный адрес: {badExtra[0]}</span>}</label>
            {plan.allowed_domains.length > 0 && <span className="help">Разрешены только адреса доменов: {plan.allowed_domains.join(", ")}.</span>}
          </fieldset>
          <div className="muted small">Формат документов — {plan.attach_format?.toUpperCase()}; если они вместе больше {plan.max_attachment_mb} МБ, в письме будет ссылка на страницу встречи. Повторная отправка допустима и записывается в аудит.</div>
          <div className="row"><button className="btn primary" disabled={busy || !plan.mail_configured || kinds.length === 0 || uniq.length === 0 || badExtra.length > 0} onClick={() => void send()}>
            {busy ? "Отправка…" : `Отправить (${uniq.length})`}</button><button className="btn ghost" onClick={onClose}>Отмена</button></div>
        </div>
      )}
    </Modal>
  );
}
