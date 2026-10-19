import { useEffect, useState } from "react";
import { api, type ApiError, type DeliveryRecipient, type DirHit, type MailDeliverySpec, type PerMaterial, type PerMode } from "../api";

export const MATERIAL_KINDS: { kind: string; label: string; help: string }[] = [
  { kind: "protocol", label: "Протокол", help: "Официальный протокол совещания (формирует языковая модель)." },
  { kind: "summary", label: "Резюме встречи", help: "Краткое резюме: суть, решения и поручения." },
  { kind: "transcript", label: "Стенограмма", help: "Полный текст реплик участников с временем." },
  { kind: "map", label: "Карта разговора", help: "Отправляется отдельным HTML-файлом независимо от выбранных форматов документов; при необходимости карта формируется перед отправкой." },
];
export const FORMAT_LABEL: Record<string, string> = { docx: "Word (.docx)", pdf: "PDF", html: "HTML", txt: "Текст (.txt)", md: "Markdown" };
export const SOURCE_LABEL: Record<string, string> = { leader: "руководитель", participant: "участник", user: "выбран вручную", manual: "свой адрес" };
export const PROBLEM_LABEL: Record<string, string> = { no_email: "в каталоге нет адреса электронной почты", invalid_email: "адрес в каталоге некорректен", domain_not_allowed: "домен запрещён политикой отправки" };
const EMAIL = /^[^\s@<>"',;]{1,64}@[^\s@<>"',;]+$/;

export const MODE_LABEL: Record<PerMode, string> = { none: "Не отправлять", leader: "Только руководителю", all: "Всем участникам встречи", selected: "Выбранным пользователям" };
const noneMaterials = (): Record<string, PerMaterial> => Object.fromEntries(MATERIAL_KINDS.map((m) => [m.kind, { mode: "none" as PerMode, users: [] }]));
/** Новые настройки: ничего не отправляется, пока руководитель сам не выберет, какой материал кому. */
export const emptySpec = (): MailDeliverySpec => ({ enabled: false, archive: false, materials: [], per_material: noneMaterials(), recipients: { leaders: false, participants: false, users: [], emails: [] } });

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
export default function DeliveryEditor({ roomId, spec, onChange, localModel = false }: { roomId: string; spec: MailDeliverySpec; onChange: (s: MailDeliverySpec) => void; localModel?: boolean }) {
  const [emailsText, setEmailsText] = useState(spec.recipients.emails.join("\n"));
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<DirHit[]>([]);
  const [hint, setHint] = useState("");
  const [preview, setPreview] = useState<{ recipients: DeliveryRecipient[]; by_material?: Record<string, DeliveryRecipient[]> | null; mail_configured: boolean; allowed_domains: string[]; participants_by_meeting: boolean } | null>(null);
  const [err, setErr] = useState("");
  const [tpls, setTpls] = useState<{ id: string; name: string; is_default: boolean }[]>([]);
  const [fmts, setFmts] = useState<string[]>(["docx", "pdf", "html", "txt", "md"]);
  useEffect(() => { void api.mailTemplateNames().then((r) => { setTpls(r.items); setFmts(r.formats); }).catch(() => undefined); }, []);
  const set = (patch: Partial<MailDeliverySpec>) => onChange({ ...spec, ...patch });
  const setRec = (patch: Partial<MailDeliverySpec["recipients"]>) => onChange({ ...spec, recipients: { ...spec.recipients, ...patch } });
  const pm: Record<string, PerMaterial> = { ...noneMaterials(), ...(spec.per_material ?? {}) };
  const setMat = (kind: string, v: PerMaterial) => {
    const next = { ...pm, [kind]: v };
    const materials = MATERIAL_KINDS.map((m) => m.kind).filter((k) => next[k].mode !== "none");
    onChange({ ...spec, per_material: next, materials, enabled: materials.length > 0 });
  };
  const anyText = MATERIAL_KINDS.some((m) => m.kind !== "map" && m.kind !== "transcript" && pm[m.kind].mode !== "none");
  const [pickFor, setPickFor] = useState("protocol");

  const tokens = emailsText.split(/[\s,;]+/).filter(Boolean);
  const bad = tokens.filter((t) => !EMAIL.test(t));
  const onEmails = (v: string) => { setEmailsText(v); setRec({ emails: v.split(/[\s,;]+/).filter((t) => EMAIL.test(t)) }); };

  const search = async () => {
    setHint("");
    try { const r = await api.manage.search(roomId, "user", q); setHits(r); if (!r.length) setHint("Ничего не найдено"); } catch (e) { setHits([]); setHint((e as ApiError).message); }
  };
  const addUser = (kind: string, h: DirHit) => {
    const cur = pm[kind];
    if (cur.users.some((u) => u.ref.toLowerCase() === h.ref.toLowerCase())) return;
    setMat(kind, { mode: "selected", users: [...cur.users, { ref: h.ref, name: h.name, email: h.email ?? "" }] });
  };
  const check = async () => {
    setErr("");
    try { setPreview(await api.manage.deliveryPreview(roomId, spec)); } catch (e) { setPreview(null); setErr((e as ApiError).message); }
  };

  return (
    <div role="tabpanel">
      <fieldset className="group"><legend>Кому какой материал отправлять</legend>
        <span className="help">По умолчанию ничего не отправляется: протокол, резюме, стенограмма и карта могут быть конфиденциальными. Для каждого материала выберите получателей. Рассылка по почте не даёт права открывать остальные материалы встречи; доступ к встрече выдаётся отдельно («Управление доступом» в истории встречи). После завершения встречи материал отправляется один раз; пересозданный материал автоматически повторно не отправляется — отправьте его вручную («Отправить материалы»), журнал покажет, что уже уходило.</span>
        {MATERIAL_KINDS.map((m) => (
          <div key={m.kind} className="picker">
            <label>{m.label}
              <select value={pm[m.kind].mode} onChange={(e) => { const mode = e.target.value as PerMode; setMat(m.kind, { mode, users: mode === "selected" ? pm[m.kind].users : [] }); if (mode === "selected") setPickFor(m.kind); }}>
                {(Object.keys(MODE_LABEL) as PerMode[]).map((k) => <option key={k} value={k}>{MODE_LABEL[k]}</option>)}
              </select><span className="help">{m.help}</span></label>
            {pm[m.kind].mode === "selected" && (
              <ul className="chips-list">{pm[m.kind].users.map((u) => (
                <li key={u.ref} title={u.email || "в каталоге не найден адрес электронной почты"}>{u.name || u.ref} <span className="muted small">{u.email || "нет e-mail"}</span>
                  <button type="button" className="chip-x" aria-label={`Убрать ${u.name}`} onClick={() => setMat(m.kind, { mode: "selected", users: pm[m.kind].users.filter((x) => x.ref !== u.ref) })}>✕</button></li>))}
                <li><button type="button" className="btn mini" onClick={() => setPickFor(m.kind)}>＋ Добавить</button></li></ul>)}
          </div>))}
        <label className="check"><input type="checkbox" checked={!!spec.archive} onChange={(e) => set({ archive: e.target.checked })} disabled={spec.materials.length < 2} />
          <span className="check-body">Отправлять одним архивом (zip)<span className="help">{spec.materials.length < 2 ? "Доступно, когда выбрано два и более материала." : "Все материалы для одних получателей кладутся в один файл."}</span></span></label>
        <span className="help">Если протокол или резюме к концу встречи ещё не готовы, система попробует сформировать их сама. Запись аудио по почте не отправляется. Большие документы не вкладываются — в письме будет ссылка на страницу встречи (после входа; открыть её сможет только тот, у кого есть доступ).</span>
      </fieldset>
      {anyText && localModel && <div className="alert warn" role="status">Протокол этой комнаты формирует встроенная локальная модель. Она небольшая и может ошибаться: документ уйдёт <b>без предварительной проверки человеком</b>. Для важных встреч лучше отправлять вручную после просмотра.</div>}

      <fieldset className="group"><legend>Письмо и документы</legend>
        <label>Шаблон письма
          <select value={spec.template_id ?? ""} onChange={(e) => set({ template_id: e.target.value || null })}>
            <option value="">По умолчанию{tpls.find((t) => t.is_default) ? ` («${tpls.find((t) => t.is_default)!.name}»)` : ""}</option>
            {tpls.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select><span className="help">Тему, текст и подпись задаёт администратор («Электронная почта → Шаблоны писем»).</span></label>
        <div><b className="small">Формат документов</b>
          <div className="checks">{fmts.map((f) => (
            <label key={f} className="check"><input type="checkbox" checked={(spec.formats ?? []).includes(f)}
              onChange={(e) => set({ formats: e.target.checked ? [...(spec.formats ?? []), f] : (spec.formats ?? []).filter((x) => x !== f) })} /> {FORMAT_LABEL[f] ?? f}</label>))}</div>
          <span className="help">Ничего не выбрано — формат по умолчанию из правил рассылки.</span></div>
        <label>Если какой-то материал не готов (например, протокол завершился ошибкой)
          <select value={spec.if_missing ?? "send"} onChange={(e) => set({ if_missing: e.target.value as "send" | "skip" })}>
            <option value="send">Отправить то, что готово (событие записывается в журнал)</option>
            <option value="skip">Не отправлять письмо (событие записывается в журнал)</option>
          </select></label>
      </fieldset>

      <fieldset className="group"><legend>Поиск сотрудников для «{MATERIAL_KINDS.find((m) => m.kind === pickFor)?.label}»</legend>
        <div className="picker">
          <div className="row">
            <select value={pickFor} onChange={(e) => setPickFor(e.target.value)} aria-label="Материал">{MATERIAL_KINDS.map((m) => <option key={m.kind} value={m.kind}>{m.label}</option>)}</select>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти сотрудника в каталоге (от 2 символов)" aria-label="Поиск сотрудника" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} />
            <button type="button" className="btn" disabled={q.trim().length < 2} onClick={() => void search()}>Найти</button>
          </div>
          {hint && <div className="muted small">{hint}</div>}
          {hits.map((h) => (
            <div key={h.ref} className="hit"><span>{h.name}{h.sam ? ` (${h.sam})` : ""} <span className="muted small">{h.email || "нет адреса электронной почты"}</span></span>
              <button type="button" className="btn mini primary" onClick={() => addUser(pickFor, h)}>＋ Добавить</button></div>))}
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
          {preview.by_material
            ? MATERIAL_KINDS.map((m) => {
                const mode = pm[m.kind].mode;
                const rows = preview.by_material?.[m.kind] ?? [];
                return (
                  <div key={m.kind} className="card" aria-label={`Получатели: ${m.label}`}>
                    <b>{m.label}</b> <span className="badge">{MODE_LABEL[mode]}</span>
                    {mode === "none" ? <div className="muted small">Автоматически не отправляется.</div>
                      : <>{mode === "all" && <div className="muted small">Участники конкретной встречи определяются после её завершения{rows.length ? "; ниже — адреса, которые уйдут в любом случае" : ""}.</div>}
                        {(rows.length > 0 || mode !== "all") && <RecipientTable rows={rows} />}</>}
                  </div>);
              })
            : <RecipientTable rows={preview.recipients} />}
          {preview.participants_by_meeting && <div className="muted small">Участники встречи определяются по факту каждой встречи — здесь их состав неизвестен.</div>}
        </div>)}
    </div>
  );
}
