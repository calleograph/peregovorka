import { useState } from "react";
import { api, type ApiError, type DeliveryRecipient, type DirHit, type MailDeliverySpec } from "../api";

export const MATERIAL_KINDS: { kind: string; label: string; help: string }[] = [
  { kind: "protocol", label: "Протокол", help: "Официальный протокол совещания (формирует языковая модель)." },
  { kind: "summary", label: "Резюме встречи", help: "Краткое резюме: суть, решения и поручения." },
  { kind: "transcript", label: "Стенограмма", help: "Полный текст реплик участников с временем." },
];
export const SOURCE_LABEL: Record<string, string> = { leader: "руководитель", participant: "участник", user: "выбран вручную", manual: "свой адрес" };
export const PROBLEM_LABEL: Record<string, string> = { no_email: "в каталоге нет адреса электронной почты", invalid_email: "адрес в каталоге некорректен", domain_not_allowed: "домен запрещён политикой отправки" };
const EMAIL = /^[^\s@<>"',;]{1,64}@[^\s@<>"',;]+$/;

export const emptySpec = (): MailDeliverySpec => ({ enabled: false, archive: false, materials: ["protocol", "summary"], recipients: { leaders: true, participants: true, users: [], emails: [] } });

/** Таблица получателей с пометками: у кого нет адреса или он запрещён политикой — видно сразу, а не «молчаливой ошибкой». */
export function RecipientTable({ rows, selected, onToggle }: { rows: DeliveryRecipient[]; selected?: Set<string>; onToggle?: (email: string) => void }) {
  if (!rows.length) return <div className="muted small">Получателей пока нет.</div>;
  return (
    <table className="table recipients">
      <tbody>{rows.map((r, i) => (
        <tr key={`${r.email}-${i}`} className={r.problem ? "has-problem" : ""}>
          {onToggle && <td style={{ width: 28 }}>{r.problem ? null : <input type="checkbox" checked={selected?.has(r.email.toLowerCase()) ?? false} onChange={() => onToggle(r.email.toLowerCase())} aria-label={`Отправить ${r.email}`} />}</td>}
          <td>{r.name || "—"}<div className="muted small">{SOURCE_LABEL[r.source] ?? r.source}</div></td>
          <td>{r.email || <span className="muted">—</span>}</td>
          <td>{r.problem ? <span className="badge warn" title={PROBLEM_LABEL[r.problem]}>{PROBLEM_LABEL[r.problem] ?? r.problem}</span> : <span className="badge ok">будет отправлено</span>}</td>
        </tr>))}</tbody>
    </table>
  );
}

/**
 * «Уведомления и доставка материалов» для руководителя комнаты: что отправлять после встречи и кому. Реквизиты почтового сервера здесь не видны и не меняются —
 * это настройка администратора системы.
 */
export default function DeliveryEditor({ roomId, spec, onChange }: { roomId: string; spec: MailDeliverySpec; onChange: (s: MailDeliverySpec) => void }) {
  const [emailsText, setEmailsText] = useState(spec.recipients.emails.join("\n"));
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<DirHit[]>([]);
  const [hint, setHint] = useState("");
  const [preview, setPreview] = useState<{ recipients: DeliveryRecipient[]; mail_configured: boolean; allowed_domains: string[]; participants_by_meeting: boolean } | null>(null);
  const [err, setErr] = useState("");
  const set = (patch: Partial<MailDeliverySpec>) => onChange({ ...spec, ...patch });
  const setRec = (patch: Partial<MailDeliverySpec["recipients"]>) => onChange({ ...spec, recipients: { ...spec.recipients, ...patch } });
  const toggleKind = (k: string) => set({ materials: spec.materials.includes(k) ? spec.materials.filter((x) => x !== k) : [...spec.materials, k] });

  const tokens = emailsText.split(/[\s,;]+/).filter(Boolean);
  const bad = tokens.filter((t) => !EMAIL.test(t));
  const onEmails = (v: string) => { setEmailsText(v); setRec({ emails: v.split(/[\s,;]+/).filter((t) => EMAIL.test(t)) }); };

  const search = async () => {
    setHint("");
    try { const r = await api.manage.search(roomId, "user", q); setHits(r); if (!r.length) setHint("Ничего не найдено"); } catch (e) { setHits([]); setHint((e as ApiError).message); }
  };
  const addUser = (h: DirHit) => {
    if (spec.recipients.users.some((u) => u.ref.toLowerCase() === h.ref.toLowerCase())) return;
    setRec({ users: [...spec.recipients.users, { ref: h.ref, name: h.name, email: h.email ?? "" }] });
  };
  const check = async () => {
    setErr("");
    try { setPreview(await api.manage.deliveryPreview(roomId, spec)); } catch (e) { setPreview(null); setErr((e as ApiError).message); }
  };

  return (
    <div role="tabpanel">
      <label className="check"><input type="checkbox" checked={spec.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        <span className="check-body">Автоматически отправлять материалы после завершения встречи<span className="help">Письма уходят выбранным получателям один раз после каждой встречи. Вручную — кнопка «Отправить материалы» на странице завершённой встречи.</span></span></label>

      <fieldset className="group"><legend>Что отправлять</legend>
        {MATERIAL_KINDS.map((m) => (
          <label key={m.kind} className="check"><input type="checkbox" checked={spec.materials.includes(m.kind)} onChange={() => toggleKind(m.kind)} />
            <span className="check-body">{m.label}<span className="help">{m.help}</span></span></label>))}
        <label className="check"><input type="checkbox" checked={!!spec.archive} onChange={(e) => set({ archive: e.target.checked })} disabled={spec.materials.length < 2} />
          <span className="check-body">Отправлять одним архивом (zip)<span className="help">Все выбранные документы кладутся в один файл — удобно, когда их несколько. {spec.materials.length < 2 ? "Доступно, когда выбрано два и более материала." : ""}</span></span></label>
        <span className="help">Если протокол или резюме к концу встречи ещё не готовы, система попробует сформировать их сама. Запись аудио по почте не отправляется. Большие документы не вкладываются — в письме будет ссылка на страницу встречи (после входа).</span>
      </fieldset>

      <fieldset className="group"><legend>Кому отправлять</legend>
        <label className="check"><input type="checkbox" checked={spec.recipients.leaders} onChange={(e) => setRec({ leaders: e.target.checked })} />
          <span className="check-body">Руководителям комнаты<span className="help">Адреса берутся из каталога; для групп руководителей — участники группы.</span></span></label>
        <label className="check"><input type="checkbox" checked={spec.recipients.participants} onChange={(e) => setRec({ participants: e.target.checked })} />
          <span className="check-body">Участникам встречи<span className="help">Тем сотрудникам, кто был на этой встрече. Гостям письма не отправляются.</span></span></label>

        <div className="picker">
          <b className="small">Конкретные сотрудники</b>
          {spec.recipients.users.length > 0 && (
            <ul className="chips-list">{spec.recipients.users.map((u) => (
              <li key={u.ref} title={u.email || "в каталоге не найден адрес электронной почты"}>{u.name || u.ref} <span className="muted small">{u.email || "нет e-mail"}</span>
                <button type="button" className="chip-x" aria-label={`Убрать ${u.name}`} onClick={() => setRec({ users: spec.recipients.users.filter((x) => x.ref !== u.ref) })}>✕</button></li>))}</ul>)}
          <div className="row">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти сотрудника в каталоге (от 2 символов)" aria-label="Поиск сотрудника" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} />
            <button type="button" className="btn" disabled={q.trim().length < 2} onClick={() => void search()}>Найти</button>
          </div>
          {hint && <div className="muted small">{hint}</div>}
          {hits.map((h) => (
            <div key={h.ref} className="hit"><span>{h.name}{h.sam ? ` (${h.sam})` : ""} <span className="muted small">{h.email || "нет адреса электронной почты"}</span></span>
              <button type="button" className="btn mini primary" onClick={() => addUser(h)}>＋ Добавить</button></div>))}
        </div>

        <label>Свои адреса электронной почты <span className="muted small">(по одному в строке или через запятую)</span>
          <textarea rows={3} value={emailsText} onChange={(e) => onEmails(e.target.value)} placeholder={"user1@example.local\npartner@example.org"} spellCheck={false} aria-invalid={bad.length > 0} />
          {bad.length > 0 && <span className="field-err" role="alert">Некорректный адрес: {bad.slice(0, 3).join(", ")}{bad.length > 3 ? "…" : ""}</span>}
        </label>
      </fieldset>

      <div className="row"><button type="button" className="btn" onClick={() => void check()}>Проверить получателей</button>
        <span className="muted small">Покажет, кому уйдёт рассылка по этим настройкам и у кого нет адреса.</span></div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {preview && (
        <div className="stack">
          {!preview.mail_configured && <div className="alert info">Исходящая почта ещё не настроена администратором системы — письма не будут отправляться, пока её не подключат.</div>}
          {preview.allowed_domains.length > 0 && <div className="muted small">Политика отправки разрешает только домены: {preview.allowed_domains.join(", ")}.</div>}
          <RecipientTable rows={preview.recipients} />
          {preview.participants_by_meeting && <div className="muted small">Участники встречи определяются по факту каждой встречи — здесь их состав неизвестен.</div>}
        </div>)}
    </div>
  );
}
