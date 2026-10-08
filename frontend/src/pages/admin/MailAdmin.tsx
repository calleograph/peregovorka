import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type DiagResult, type MailMessageRow, type MailProfile } from "../../api";
import { SecretInput, StageList, fmtDate, secretToSend } from "./common";

interface Form {
  id?: string; name: string; host: string; port: number; security: "none" | "starttls" | "ssl"; auth_type: "none" | "login"; username: string; secret: string; secret_set: boolean;
  from_address: string; from_name: string; timeout_s: number; verify_cert: boolean;
}
const PORTS = { none: 25, starttls: 587, ssl: 465 } as const;
const empty: Form = { name: "", host: "", port: 587, security: "starttls", auth_type: "none", username: "", secret: "", secret_set: false, from_address: "", from_name: "Peregovorka", timeout_s: 15, verify_cert: true };
const toForm = (p: MailProfile): Form => ({ ...p, secret: "" });

/**
 * Исходящая почта (SMTP): достаточно для Exchange и обычного SMTP-relay. Пароль не показывается — «задан / изменить». «Проверить соединение» проходит DNS →
 * подключение → защита (сертификат) → вход и объясняет причину сбоя; «Отправить тестовое письмо» отправляет письмо на введённый адрес. Реквизиты видят только
 * администраторы системы — руководители комнат лишь выбирают «что и кому» отправлять.
 */
export default function MailAdmin({ onOpen }: { onOpen?: (id: string) => void }) {
  const [items, setItems] = useState<MailProfile[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [diag, setDiag] = useState<Record<string, DiagResult | "run">>({});
  const [to, setTo] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<Record<string, { ok: boolean; text: string } | "run">>({});

  const load = useCallback(() => api.admin.mailProfiles().then((r) => setItems(r.items)).catch((e) => setError((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  const body = (f: Form) => ({
    name: f.name.trim(), host: f.host.trim(), port: f.port, security: f.security, auth_type: f.auth_type, username: f.auth_type === "login" ? f.username.trim() : "",
    from_address: f.from_address.trim(), from_name: f.from_name.trim(), timeout_s: f.timeout_s, verify_cert: f.verify_cert,
    ...(f.auth_type === "login" && secretToSend(f.secret) !== undefined ? { secret: secretToSend(f.secret) } : {}),
  });

  const check = async (id: string) => {
    setDiag((d) => ({ ...d, [id]: "run" }));
    try { const r = await api.admin.checkMail(id); setDiag((d) => ({ ...d, [id]: r })); }
    catch (e) { setDiag((d) => ({ ...d, [id]: { ok: false, stages: [], message: (e as ApiError).message } })); }
  };
  const testSend = async (id: string) => {
    setSent((s) => ({ ...s, [id]: "run" }));
    try { const r = await api.admin.testMail(id, (to[id] ?? "").trim()); setSent((s) => ({ ...s, [id]: { ok: r.ok, text: r.message } })); }
    catch (e) { setSent((s) => ({ ...s, [id]: { ok: false, text: (e as ApiError).message } })); }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setError(""); setNote(""); setBusy(true);
    try {
      if (form.id) await api.admin.updateMail(form.id, body(form)); else await api.admin.createMail(body(form));
      setForm(null); setNote("Сохранено. Нажмите «Проверить соединение» и «Отправить тестовое письмо»."); await load();
    } catch (err) { setError((err as ApiError).message); }
    setBusy(false);
  };
  const remove = async (p: MailProfile) => {
    if (!window.confirm(`Удалить профиль «${p.name}»? Если он единственный, письма перестанут отправляться.`)) return;
    try { await api.admin.deleteMail(p.id); await load(); } catch (e) { setError((e as ApiError).message); }
  };
  const activate = async (p: MailProfile) => { try { await api.admin.activateMail(p.id); await load(); } catch (e) { setError((e as ApiError).message); } };

  return (
    <section>
      <div className="row"><h2>Исходящая почта</h2><div className="spacer" />
        {!form && <button className="btn primary" onClick={() => { setForm({ ...empty }); setError(""); setNote(""); }}>＋ Добавить профиль</button>}</div>
      <p className="muted">Через этот сервер уходят материалы встреч. Профилей может быть несколько — письма отправляет <b>активный</b>. Если сервер использует внутренний сертификат,
        загрузите его CA в разделе {onOpen ? <a href="#ca" onClick={(e) => { e.preventDefault(); onOpen("ca"); }}>«Сертификаты (CA)»</a> : "«Сертификаты (CA)»"}.</p>
      {!items.length && !form && <div className="alert info">Почта не настроена: материалы встреч по почте отправляться не будут. Добавьте профиль SMTP.</div>}
      {note && <div className="alert ok" role="status">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}

      {form && (
        <form className="card form" onSubmit={(e) => void save(e)} noValidate>
          <h3 style={{ margin: 0 }}>{form.id ? `Изменение профиля «${form.name}»` : "Новый профиль SMTP"}</h3>
          <label>Название<input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Почтовый сервер организации" maxLength={120} /></label>
          <fieldset className="group"><legend>Сервер</legend>
            <div className="cols">
              <label>Сервер SMTP<input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="smtp.example.local" autoCapitalize="none" spellCheck={false} /></label>
              <label>Защита соединения
                <select value={form.security} onChange={(e) => { const v = e.target.value as Form["security"]; setForm((f) => (f ? { ...f, security: v, port: PORTS[v] } : f)); }}>
                  <option value="starttls">STARTTLS (обычно порт 587)</option><option value="ssl">SSL/TLS (обычно порт 465)</option><option value="none">Без шифрования (порт 25, только внутри сети)</option>
                </select></label>
              <label>Порт<input type="number" min={1} max={65535} value={form.port} onChange={(e) => set("port", Number(e.target.value))} style={{ maxWidth: 140 }} /></label>
              <label>Таймаут<input type="number" min={3} max={120} value={form.timeout_s} onChange={(e) => set("timeout_s", Number(e.target.value))} style={{ maxWidth: 140 }} /><span className="help">секунд</span></label>
            </div>
            {form.security === "none" && <div className="alert info small">Без шифрования письма (и пароль, если он задан) идут открытым текстом. Используйте только для внутреннего relay в доверенной сети.</div>}
          </fieldset>
          <fieldset className="group"><legend>Аутентификация</legend>
            <label>Способ
              <select value={form.auth_type} onChange={(e) => set("auth_type", e.target.value as Form["auth_type"])}>
                <option value="none">Без аутентификации (relay по адресу сервера)</option><option value="login">Логин и пароль</option>
              </select></label>
            {form.auth_type === "login" && (
              <div className="cols">
                <label>Логин<input value={form.username} onChange={(e) => set("username", e.target.value)} placeholder="svc-mail@example.local" autoComplete="off" spellCheck={false} /></label>
                <SecretInput label="Пароль" isSet={form.secret_set} value={form.secret} onChange={(v) => set("secret", v)} help="Хранится в зашифрованном виде и после сохранения не показывается." />
              </div>
            )}
          </fieldset>
          <fieldset className="group"><legend>Отправитель</legend>
            <div className="cols">
              <label>Адрес отправителя<input value={form.from_address} onChange={(e) => set("from_address", e.target.value)} placeholder="noreply@example.local" autoCapitalize="none" spellCheck={false} />
                <span className="help">Как правило, noreply-адрес. Сервер должен разрешать отправку от него.</span></label>
              <label>Отображаемое имя<input value={form.from_name} onChange={(e) => set("from_name", e.target.value)} placeholder="Peregovorka" maxLength={200} /></label>
            </div>
          </fieldset>
          <label className="check"><input type="checkbox" checked={form.verify_cert} onChange={(e) => set("verify_cert", e.target.checked)} />
            <span className="check-body">Проверять сертификат сервера почты<span className="help">Рекомендуется. Отключайте только временно и только для внутреннего сервера с самоподписанным сертификатом — лучше загрузите его CA.</span></span></label>
          <div className="row form-actions"><button className="btn primary" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</button><button type="button" className="btn ghost" onClick={() => setForm(null)}>Отмена</button></div>
        </form>
      )}

      {items.map((p) => {
        const d = diag[p.id]; const s = sent[p.id];
        return (
          <div key={p.id} className="card conn-card">
            <div className="row"><h3 style={{ margin: 0 }}>{p.name}</h3>{p.is_active ? <span className="badge ok">активный</span> : <span className="badge">резервный</span>}
              {!p.verify_cert && <span className="badge warn">сертификат не проверяется</span>}</div>
            <div className="muted small"><code>{p.host}:{p.port}</code> · {p.security === "ssl" ? "SSL/TLS" : p.security === "starttls" ? "STARTTLS" : "без шифрования"} · {p.auth_type === "login" ? <>вход <code>{p.username}</code>, пароль {p.secret_set ? "задан" : "не задан"}</> : "без аутентификации"} · от <code>{p.from_name ? `${p.from_name} <${p.from_address}>` : p.from_address}</code></div>
            <div className="row form-actions">
              <button className="btn primary" disabled={d === "run"} onClick={() => void check(p.id)}>{d === "run" ? "Проверка…" : "Проверить соединение"}</button>
              <button className="btn" onClick={() => { setForm(toForm(p)); setError(""); setNote(""); }}>Изменить</button>
              {!p.is_active && <button className="btn" onClick={() => void activate(p)}>Сделать активным</button>}
              <button className="btn ghost danger" onClick={() => void remove(p)}>Удалить</button>
            </div>
            {d && d !== "run" && <StageList result={d} />}
            <div className="row tight test-send">
              <input type="email" value={to[p.id] ?? ""} onChange={(e) => setTo((t) => ({ ...t, [p.id]: e.target.value }))} placeholder="Адрес получателя тестового письма: user1@example.local" aria-label="Адрес для тестового письма" />
              <button className="btn" disabled={s === "run" || !(to[p.id] ?? "").includes("@")} onClick={() => void testSend(p.id)}>{s === "run" ? "Отправка…" : "Отправить тестовое письмо"}</button>
            </div>
            {s && s !== "run" && <div className={`alert ${s.ok ? "ok" : "error"}`} role="status">{s.ok ? "✓ " : "✗ "}{s.text}</div>}
          </div>
        );
      })}
    </section>
  );
}

const STATE_LABEL: Record<string, [string, string]> = { queued: ["в очереди", "badge"], sending: ["отправляется", "badge"], sent: ["отправлено", "badge ok"], failed: ["ошибка", "badge warn"] };
const KIND_LABEL: Record<string, string> = { protocol: "протокол", summary: "резюме", transcript: "стенограмма" };

/** Журнал отправки: время, комната/встреча, получатель, результат и последняя ошибка. Содержимое документов здесь не хранится и не показывается. */
export function MailLogAdmin() {
  const [rows, setRows] = useState<MailMessageRow[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [state, setState] = useState("");
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => api.admin.mailMessages(state, q).then((r) => { setRows(r.items); setCounts(r.counts); }).catch((e) => setError((e as ApiError).message)), [state, q]);
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 8000); return () => window.clearInterval(t); }, [load]);
  const retry = async (id: string) => { try { await api.admin.retryMail(id); await load(); } catch (e) { setError((e as ApiError).message); } };
  return (
    <section>
      <div className="row"><h2>Журнал отправки писем</h2><div className="spacer" /><button className="btn" onClick={() => void load()}>Обновить</button></div>
      <p className="muted">Каждое письмо проходит очередь: в очереди → отправляется → отправлено / ошибка. При временной ошибке сервера отправка повторяется с нарастающей паузой (число повторов — в «Правилах рассылки»).</p>
      <div className="row tight">
        {(["", "queued", "sending", "sent", "failed"] as const).map((s) => (
          <button key={s || "all"} className={`btn mini ${state === s ? "primary" : ""}`} onClick={() => setState(s)}>{s ? STATE_LABEL[s][0] : "все"}{s && counts[s] !== undefined ? ` · ${counts[s]}` : ""}</button>
        ))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск: получатель или комната" aria-label="Поиск" style={{ maxWidth: 320 }} />
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="table-scroll"><table className="table">
        <thead><tr><th>Время</th><th>Комната</th><th>Получатель</th><th>Материалы</th><th>Результат</th><th /></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={6} className="muted">Писем нет.</td></tr>}
          {rows.map((m) => (
            <tr key={m.id}>
              <td className="small">{fmtDate(m.created_at)}{m.sent_at && <div className="muted">отпр. {fmtDate(m.sent_at)}</div>}</td>
              <td>{m.room ?? "—"}<div className="muted small">{m.trigger === "manual" ? `вручную: ${m.requested_by ?? ""}` : "автоматически"}</div></td>
              <td>{m.recipient_name && m.recipient_name !== m.recipient ? <>{m.recipient_name}<div className="muted small">{m.recipient}</div></> : m.recipient}</td>
              <td className="small">{m.kinds.map((k) => KIND_LABEL[k] ?? k).join(", ")}{m.delivery && <div className="muted">{m.delivery === "link" ? "ссылкой" : "вложением"}</div>}</td>
              <td><span className={STATE_LABEL[m.state][1]}>{STATE_LABEL[m.state][0]}</span>{m.attempts > 0 && m.state !== "sent" && <span className="muted small"> попытка {m.attempts}/{m.max_attempts}</span>}
                {m.state === "queued" && m.attempts > 0 && <div className="muted small">повтор {fmtDate(m.next_attempt_at)}</div>}
                {m.last_error && <div className="small err" title={m.last_error}>{m.last_error}</div>}</td>
              <td>{(m.state === "failed" || (m.state === "queued" && m.attempts > 0)) && <button className="btn mini" onClick={() => void retry(m.id)}>Повторить</button>}</td>
            </tr>))}
        </tbody>
      </table></div>
    </section>
  );
}
