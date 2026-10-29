import { useEffect, useState } from "react";
import { api, type ApiError, type SigningAgent, type SigningCert, type SigningJob, type SigningOverview } from "../../api";
import { Icon } from "../../components/Icons";
import { fmtDate } from "./common";
import { useFocusOnMount } from "../../useInitialFocus";
import InfoTip from "../../components/InfoTip";

/** «Windows-клиент → Подпись и корпоративные сборки»: подписывающие узлы и их сертификаты, корпоративные установщики, канал обновлений, диагностика,
 *  инструкции. Закрытые ключи, пароли PFX и токены агентов здесь не показываются и через браузер не передаются вовсе. */
const TABS: [string, string][] = [["certs", "Сертификаты"], ["builds", "Корпоративные установщики"], ["updates", "Обновления"], ["diag", "Диагностика подписи"], ["guide", "Инструкции"]];
const STATE: Record<string, [string, string]> = {
  ok: ["ok", "действует"], retired: ["", "заменён"], expired: ["error", "срок истёк"], not_yet_valid: ["warn", "ещё не действует"], invalid: ["error", "не годится для подписи"],
  cannot_sign: ["error", "пробная подпись не удалась"], untrusted_chain: ["warn", "цепочка не проверена"],
};
const JOB_KIND: Record<string, string> = { create_cert: "Создание сертификата", register_cert: "Подключение сертификата", check_cert: "Проверка сертификата", build: "Корпоративный выпуск" };
const JOB_STATUS: Record<string, [string, string]> = { queued: ["", "в очереди"], running: ["warn", "выполняется"], done: ["ok", "готово"], failed: ["error", "ошибка"], cancelled: ["", "отменено"] };
const yesNo = (v: boolean | undefined, yes: string, no: string) => (v === undefined ? "—" : v ? yes : no);
const cn = (dn: string) => /CN=([^,]+)/.exec(dn)?.[1] ?? dn;

function Copy({ text, label = "Скопировать" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="btn mini" onClick={() => { void navigator.clipboard.writeText(text).then(() => { setDone(true); window.setTimeout(() => setDone(false), 1500); }); }}
            aria-label={`${label}: ${text.slice(0, 40)}`}>
      <Icon name={done ? "check" : "copy"} size={14} />{done ? "Скопировано" : label}
    </button>
  );
}

function Code({ children }: { children: string }) {
  return (
    <div className="sg-code">
      <pre className="cmd">{children}</pre>
      <Copy text={children} />
    </div>
  );
}

function Badge({ kind, text }: { kind: string; text: string }) {
  return <span className={`badge ${kind}`}>{text}</span>;
}

export default function SigningAdmin() {
  const [tab, setTab] = useState("certs");
  const [ov, setOv] = useState<SigningOverview | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.signing.overview().then((o) => { setOv(o); setErr(""); }).catch((e) => setErr((e as ApiError).message));
  useEffect(() => { void load(); }, []);
  const active = ov?.jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(t);
  }, [active]);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setErr(""); setNote("");
    try { await fn(); setNote(ok); } catch (e) { setErr((e as ApiError).message); }
    await load();
    setBusy(false);
  };
  if (!ov) return <p className="muted">{err || "Загрузка…"}</p>;
  const props = { ov, busy, act };
  return (
    <section aria-label="Подпись и корпоративные сборки" className="signing">
      <h2>Подпись и корпоративные сборки</h2>
      <p className="muted">Установщики приложения Windows подписываются на <b>подписывающем узле</b> — отдельном доверенном компьютере Windows организации с агентом Peregovorka.
        Закрытый ключ остаётся в хранилище сертификатов этого компьютера: сервер хранит только сведения о сертификате, его открытую часть и готовые пакеты.</p>
      <div className="tabs" role="tablist" aria-label="Разделы подписи">
        {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {note && <div className="alert ok" role="status">{note}</div>}
      {tab === "certs" && <CertsTab {...props} />}
      {tab === "builds" && <BuildsTab {...props} />}
      {tab === "updates" && <UpdatesTab {...props} />}
      {tab === "diag" && <DiagTab ov={ov} />}
      {tab === "guide" && <Guide server={location.origin} />}
    </section>
  );
}

type TabProps = { ov: SigningOverview; busy: boolean; act: (fn: () => Promise<unknown>, ok: string) => Promise<void> };

// ---------------------------------------------------------------------------------------------- сертификаты и узлы
function CertsTab({ ov, busy, act }: TabProps) {
  const focusOnMount = useFocusOnMount();
  const agents = ov.agents.filter((a) => !a.revoked);
  const [enroll, setEnroll] = useState<{ code: string; server: string } | null>(null);
  const [nodeName, setNodeName] = useState("Узел подписи");
  const [form, setForm] = useState<"" | "create" | "register">("");
  const [agentId, setAgentId] = useState("");
  const [subject, setSubject] = useState("");
  const [years, setYears] = useState(3);
  const [thumb, setThumb] = useState("");
  const agent = agentId || agents[0]?.id || "";
  const busyJob = (kind: string) => ov.jobs.find((j) => j.kind === kind && (j.status === "queued" || j.status === "running"));
  return (
    <>
      <div className="card form">
        <div className="row"><h3>Подписывающие узлы</h3><div className="spacer" />
          <label className="inline">Название<input value={nodeName} onChange={(e) => setNodeName(e.target.value)} maxLength={120} /></label>
          <button className="btn" disabled={busy} onClick={() => void act(async () => { const r = await api.signing.enroll(nodeName); setEnroll(r); }, "Код подключения создан")}>
            <Icon name="plus" size={16} />Подключить узел</button>
        </div>
        {enroll && (
          <div className="alert info">
            <p>Выполните на подписывающем узле (Windows, PowerShell от имени администратора). Код одноразовый и действует час.</p>
            <Code>{`.\\Peregovorka-Signer.ps1 Enroll -Server ${enroll.server} -Code ${enroll.code} -Name "${nodeName}"\n.\\Peregovorka-Signer.ps1 Install`}</Code>
            <p className="help">Сценарий агента — <code>desktop/signing/Peregovorka-Signer.ps1</code> из комплекта Peregovorka. Токен агента после подключения хранится на узле, защищённым DPAPI.</p>
          </div>)}
        {ov.agents.length === 0 ? <p className="muted">Узлов пока нет. Подключите доверенный компьютер Windows, на котором будет храниться ключ подписи.</p> : (
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Узел</th><th>Связь</th><th>Агент</th><th>Источник выпусков</th><th /></tr></thead>
            <tbody>{ov.agents.map((a) => (
              <tr key={a.id}>
                <td><b>{a.name}</b><div className="muted small">{a.host || "—"}</div></td>
                <td>{a.revoked ? <Badge kind="" text="отключён" /> : a.online ? <Badge kind="ok" text="на связи" /> : <Badge kind="warn" text={`был ${fmtDate(a.last_seen_at)}`} />}</td>
                <td className="small">{a.version || "—"}<div className="muted">{a.info.os ?? ""}</div></td>
                <td className="small sg-fp">{a.info.release_base ?? "—"}</td>
                <td className="actions">{!a.revoked && <button className="btn mini danger" disabled={busy}
                  onClick={() => { if (window.confirm(`Отключить узел «${a.name}»? Его токен перестанет действовать, невыполненные задания отменятся.`)) void act(() => api.signing.revokeAgent(a.id), "Узел отключён"); }}>Отключить</button>}</td>
              </tr>))}</tbody>
          </table></div>)}
      </div>

      <div className="card form">
        <div className="row"><h3>Сертификаты подписи</h3><div className="spacer" />
          <button className={`btn ${form === "create" ? "primary" : ""}`} disabled={!agents.length} onClick={() => setForm(form === "create" ? "" : "create")}>Создать собственный сертификат</button>
          <button className={`btn ${form === "register" ? "primary" : ""}`} disabled={!agents.length} onClick={() => setForm(form === "register" ? "" : "register")}>Подключить сертификат компании</button>
        </div>
        {form && (
          <form ref={focusOnMount} className="sg-form" onSubmit={(e) => {
            e.preventDefault();
            void act(() => (form === "create" ? api.signing.createCert(agent, subject, years) : api.signing.registerCert(agent, thumb)),
              "Задание отправлено узлу — результат появится здесь через несколько секунд").then(() => setForm(""));
          }}>
            <div className="cols">
              <label>Подписывающий узел<select value={agent} onChange={(e) => setAgentId(e.target.value)}>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
              {form === "create" ? (<>
                <label>Издатель (название организации)<input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={120} required placeholder="ООО «Пример»" /></label>
                <label>Срок действия<select value={years} onChange={(e) => setYears(Number(e.target.value))}>{[1, 2, 3, 5].map((y) => <option key={y} value={y}>{y} {y === 1 ? "год" : y < 5 ? "года" : "лет"}</option>)}</select></label>
              </>) : (
                <label>Отпечаток сертификата (SHA-1)<input value={thumb} onChange={(e) => setThumb(e.target.value)} required className="sg-fp" placeholder="40 шестнадцатеричных символов"
                  pattern="[0-9A-Fa-f\s:]{40,59}" /></label>)}
            </div>
            <p className="help">{form === "create"
              ? "Самоподписанный сертификат: RSA 3072, SHA-256, назначение «Подпись кода», ключ не экспортируется. Компьютеры сотрудников ему не доверяют, пока вы не распространите его открытую часть групповой политикой (вкладка «Инструкции»)."
              : "Сертификат, выпущенный УЦ организации (например, AD CS), уже должен быть установлен на узле — в хранилище «Локальный компьютер → Личное». Файл PFX и его пароль сюда не вводятся: импорт — на самом узле командой ImportPfx."}</p>
            <div className="row"><button className="btn primary" disabled={busy || !agent || !!busyJob(form === "create" ? "create_cert" : "register_cert")}>{form === "create" ? "Создать" : "Подключить"}</button>
              <button type="button" className="btn" onClick={() => setForm("")}>Отмена</button></div>
          </form>)}
        {ov.certs.length === 0 ? <p className="muted">Сертификатов пока нет.</p> : <div className="sg-list">{ov.certs.map((c) => <CertCard key={c.id} c={c} agent={ov.agents.find((a) => a.id === c.agent_id)} busy={busy} act={act}
          checking={!!ov.jobs.find((j) => j.cert_id === c.id && j.kind === "check_cert" && (j.status === "queued" || j.status === "running"))} />)}</div>}
        <JobsList jobs={ov.jobs.filter((j) => j.kind !== "build").slice(0, 5)} agents={ov.agents} busy={busy} act={act} />
      </div>
    </>
  );
}

function CertCard({ c, agent, busy, act, checking }: { c: SigningCert; agent?: SigningAgent; busy: boolean; act: TabProps["act"]; checking: boolean }) {
  const [kind, text] = STATE[c.state] ?? ["", c.state];
  const k = c.check;
  return (
    <article className={`sg-cert ${c.active ? "active" : ""}`} aria-label={`Сертификат ${cn(c.subject)}`}>
      <header className="row">
        <div className="sg-title"><b>{cn(c.subject)}</b><span className="muted small">{c.kind === "self" ? "собственный (самоподписанный)" : "сертификат организации"} · узел «{agent?.name ?? "—"}»</span></div>
        <div className="spacer" />
        {c.active && <Badge kind="ok" text="активный: подписывает сборки" />}
        <Badge kind={kind} text={text} />
      </header>
      <dl className="sg-kv">
        <dt>Издатель</dt><dd>{c.subject}</dd>
        <dt>Кем выдан</dt><dd>{c.issuer}{k.self_signed ? <span className="muted"> (сам собой)</span> : null}</dd>
        <dt>Серийный номер</dt><dd className="sg-fp">{c.serial}</dd>
        <dt>Отпечаток SHA-256</dt><dd className="sg-fp">{c.sha256} <Copy text={c.sha256} /></dd>
        <dt>Отпечаток SHA-1</dt><dd className="sg-fp">{c.thumbprint}</dd>
        <dt>Назначение</dt><dd>{yesNo(k.code_signing, "Подпись кода (1.3.6.1.5.5.7.3.3)", "нет назначения «Подпись кода»")}</dd>
        <dt>Действует</dt><dd>{fmtDate(c.not_before)} — {fmtDate(c.not_after)} <span className={c.days_left < 30 ? "sg-warn" : "muted"}>({c.days_left >= 0 ? `осталось ${c.days_left} дн.` : "истёк"})</span></dd>
        <dt>Ключ</dt><dd>{c.key_alg || "—"}, {c.sig_alg || "—"}</dd>
        <dt>Закрытый ключ на узле</dt><dd>{yesNo(k.has_private_key, "доступен", "НЕ найден")}{k.key_exportable === false ? " · не экспортируется" : k.key_exportable ? " · экспортируемый" : ""}</dd>
        <dt>Цепочка на узле</dt><dd>{yesNo(k.chain_ok, "доверена", "не доверена")}{k.chain_status ? <div className="muted small">{k.chain_status}</div> : null}</dd>
        <dt>Пробная подпись</dt><dd>{yesNo(k.can_sign, "выполнена", "не удалась")} <span className="muted small">· проверено {fmtDate(c.checked_at)}</span></dd>
      </dl>
      <div className="row">
        <button className="btn mini" disabled={busy || checking || !agent?.online} onClick={() => void act(() => api.signing.checkCert(c.id), "Проверка отправлена узлу")}>{checking ? "Проверяется…" : "Проверить сертификат"}</button>
        {c.has_cer && <a className="btn mini" href={api.signing.cerUrl(c.id)} download><Icon name="download" size={14} />Скачать публичный сертификат (.cer)</a>}
        {!c.active && c.state === "ok" && <button className="btn mini" disabled={busy} onClick={() => {
          if (window.confirm("Новые сборки будут подписываться этим сертификатом. Уже установленные клиенты продолжат обновляться, если имя издателя совпадает (см. «Инструкции → Замена сертификата»).")) void act(() => api.signing.activateCert(c.id), "Сертификат назначен активным");
        }}>{activateLabel(c)}</button>}
      </div>
    </article>
  );
}
const activateLabel = (c: SigningCert) => (c.retired ? "Вернуть в работу" : "Сделать активным / заменить текущий");

function JobsList({ jobs, agents, busy, act }: { jobs: SigningJob[]; agents: SigningAgent[]; busy: boolean; act: TabProps["act"] }) {
  if (!jobs.length) return null;
  return (
    <div className="table-scroll"><table className="table">
      <thead><tr><th>Задание</th><th>Узел</th><th>Состояние</th><th>Создано</th><th /></tr></thead>
      <tbody>{jobs.map((j) => {
        const [k, t] = JOB_STATUS[j.status];
        return (
          <tr key={j.id}>
            <td>{JOB_KIND[j.kind]}<div className="muted small">{String(j.params.subject ?? j.params.thumbprint ?? j.params.org_name ?? "")}</div></td>
            <td className="small who">{agents.find((a) => a.id === j.agent_id)?.name ?? "—"}</td>
            <td>{j.status === "running" || j.status === "queued" ? <span className="row tight"><span className="sg-spin" aria-hidden />{j.progress || t}</span> : <Badge kind={k} text={t} />}
              {j.error && <div className="sg-err small">{j.error}</div>}</td>
            <td className="small when">{fmtDate(j.created_at)}<div className="muted">{j.created_by}</div></td>
            <td className="actions">{(j.status === "queued" || j.status === "running") && <button className="btn mini" disabled={busy} onClick={() => void act(() => api.signing.cancel(j.id), "Задание отменено")}>Отменить</button>}</td>
          </tr>);
      })}</tbody>
    </table></div>
  );
}

// ---------------------------------------------------------------------------------------------- корпоративные установщики
function BuildsTab({ ov, busy, act }: TabProps) {
  const usable = ov.certs.filter((c) => c.state === "ok");
  const activeCert = usable.find((c) => c.active) ?? usable[0];
  const [f, setF] = useState({ org_name: "", server_url: location.origin.startsWith("https://") ? location.origin : "https://", version: ov.channel.published?.version ?? "",
    types: ["exe", "msi"], update_policy: ov.channel.published ? "channel" : "notify", autostart: "tray", allow_add: false, cert_id: "" });
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));
  const cert = f.cert_id || activeCert?.id || "";
  const builds = ov.jobs.filter((j) => j.kind === "build");
  const toggle = (t: string) => set({ types: f.types.includes(t) ? f.types.filter((x) => x !== t) : [...f.types, t] });
  return (
    <>
      <form className="card form" onSubmit={(e) => { e.preventDefault(); void act(() => api.signing.build({ ...f, cert_id: cert }), "Выпуск поставлен узлу подписи"); }}>
        <h3>Новый корпоративный выпуск</h3>
        <p className="help">Узел берёт проверенную версию приложения из своего источника выпусков, сверяет SHA-256 и подпись издателя, вписывает настройки организации в MSI,
          подписывает EXE и MSI выбранным сертификатом и проверяет подписи. Отдельный код приложения для организации не создаётся.</p>
        {!usable.length && <div className="alert warn">Нет действующего сертификата подписи — создайте или подключите его на вкладке «Сертификаты».</div>}
        <div className="cols">
          <label>Организация (так подключение будет подписано у сотрудников)<input value={f.org_name} onChange={(e) => set({ org_name: e.target.value })} maxLength={60} required placeholder="Головной офис" /></label>
          <label>Адрес сервера Peregovorka<input value={f.server_url} onChange={(e) => set({ server_url: e.target.value })} required placeholder="https://meet.example.org" pattern="https://[^/\s]+/?" /></label>
          <label><span className="field-title">Версия клиента<InfoTip>Номер выпуска издателя (например, из вкладки «Обновления»).</InfoTip></span><input value={f.version} onChange={(e) => set({ version: e.target.value })} required placeholder="1.0.1" pattern="\d+\.\d+\.\d+" />
            </label>
          <label>Сертификат подписи<select value={cert} onChange={(e) => set({ cert_id: e.target.value })} required>
            {usable.map((c) => <option key={c.id} value={c.id}>{cn(c.subject)} · {c.kind === "self" ? "собственный" : "организации"} · до {fmtDate(c.not_after).split(",")[0]}</option>)}</select></label>
          <label>Обновления<select value={f.update_policy} onChange={(e) => set({ update_policy: e.target.value })}>
            <option value="channel">Корпоративный канал этого сервера (версию выпускает администратор)</option>
            <option value="notify">Канал издателя: проверять и сообщать</option>
            <option value="auto">Канал издателя: загружать автоматически</option>
            <option value="off">Не проверять (обновляет администратор)</option></select></label>
          <label>Автозапуск<select value={f.autostart} onChange={(e) => set({ autostart: e.target.value })}>
            <option value="tray">Свёрнутым в трей</option><option value="window">С открытым окном</option><option value="off">Не запускать</option></select></label>
        </div>
        <div className="row">
          <label className="check"><input type="checkbox" checked={f.types.includes("msi")} onChange={() => toggle("msi")} />MSI (групповые политики, SCCM)</label>
          <label className="check"><input type="checkbox" checked={f.types.includes("exe")} onChange={() => toggle("exe")} />EXE (установка пользователем)</label>
          <label className="check"><input type="checkbox" checked={f.allow_add} onChange={(e) => set({ allow_add: e.target.checked })} />Разрешить сотрудникам добавлять другие организации</label>
        </div>
        <p className="help">Значок организации приложение берёт с её сервера («Настройки сайта»). Для EXE настройки поставляются файлом политик <code>.reg</code> (или групповой политикой по шаблону ADMX) — сам EXE после подписи не меняется.</p>
        <div className="form-actions"><button className="btn primary" disabled={busy || !cert || !f.types.length}>Создать корпоративный выпуск</button></div>
      </form>
      {builds.map((j) => <BuildCard key={j.id} j={j} ov={ov} busy={busy} act={act} />)}
    </>
  );
}

function BuildCard({ j, ov, busy, act }: { j: SigningJob; ov: SigningOverview; busy: boolean; act: TabProps["act"] }) {
  const [k, t] = JOB_STATUS[j.status];
  const p = j.params as Record<string, string | string[] | boolean>;
  const c = ov.certs.find((x) => x.id === j.cert_id);
  return (
    <article className="card sg-build" aria-label={`Выпуск для ${String(p.org_name)}`}>
      <header className="row">
        <div className="sg-title"><b>{String(p.org_name)} · версия {String(p.version)}</b><span className="muted small">{String(p.server_url)} · {(p.types as string[]).join(", ").toUpperCase()} · {fmtDate(j.created_at)}, {j.created_by}</span></div>
        <div className="spacer" />
        {j.status === "running" || j.status === "queued" ? <span className="row tight"><span className="sg-spin" aria-hidden /><span className="small">{j.progress || t}</span></span> : <Badge kind={k} text={t} />}
        {(j.status === "queued" || j.status === "running") && <button className="btn mini" disabled={busy} onClick={() => void act(() => api.signing.cancel(j.id), "Задание отменено")}>Отменить</button>}
      </header>
      {j.error && <div className="alert error" role="alert">{j.error}</div>}
      {j.status === "done" && (<>
        <div className="alert ok">Подписано сертификатом «{c ? cn(c.subject) : String(p.thumbprint)}». Подписи проверены на узле; SHA-256 сверены сервером при приёме.</div>
        <div className="table-scroll"><table className="table sg-files">
          <thead><tr><th>Файл</th><th>Размер</th><th>Подпись</th><th>SHA-256</th><th /></tr></thead>
          <tbody>{j.files.map((f) => (
            <tr key={f.name}>
              <td><b>{f.name}</b></td>
              <td className="small">{f.size > 1e6 ? `${(f.size / 1048576).toFixed(1)} МБ` : `${Math.max(1, Math.round(f.size / 1024))} КБ`}</td>
              <td>{f.signature ? <Badge kind={f.signature.status === "Valid" ? "ok" : "error"} text={f.signature.status === "Valid" ? "действительна" : f.signature.status} /> : <span className="muted small">не требуется</span>}</td>
              <td className="sg-fp small">{f.sha256}</td>
              <td className="actions"><a className="btn mini" href={api.signing.fileUrl(j.id, f.name)} download><Icon name="download" size={14} />Скачать</a></td>
            </tr>))}</tbody>
        </table></div>
      </>)}
    </article>
  );
}

// ---------------------------------------------------------------------------------------------- обновления
function UpdatesTab({ ov, busy, act }: TabProps) {
  const ch = ov.channel;
  const [feed, setFeed] = useState(ch.feed_url);
  const [keys, setKeys] = useState(ch.pubkeys);
  const [found, setFound] = useState<{ version: string; released: string; notes: string } | null>(null);
  return (
    <>
      <div className="card form">
        <h3>Канал издателя</h3>
        <p className="help">Обновления приложения подписывает издатель отдельным ключом (Ed25519) — это не сертификат подписи установщиков. Здесь вводится только
          <b> открытый</b> ключ проверки; закрытый ключ обновлений в Peregovorka не вводится и не хранится.</p>
        <div className="cols">
          <label>Адрес канала (update.json)<input value={feed} onChange={(e) => setFeed(e.target.value)} /></label>
          <label>Открытый ключ проверки (base64; при смене — старый и новый через запятую)<input value={keys} onChange={(e) => setKeys(e.target.value)} className="sg-fp" placeholder="DESKTOP_UPDATE_PUBKEY издателя" /></label>
        </div>
        <div className="row">
          <button className="btn" disabled={busy} onClick={() => void act(() => api.signing.channelSave({ feed_url: feed, pubkeys: keys }), "Сохранено")}>Сохранить</button>
          <button className="btn" disabled={busy || !ch.pubkeys} onClick={() => void act(async () => setFound(await api.signing.channelCheck()), "Подпись манифеста издателя верна")}>Проверить канал издателя</button>
        </div>
        {found && <div className="alert ok">Доступна версия <b>{found.version}</b> от {fmtDate(found.released)} — подпись издателя верна.{found.notes && <pre className="cmd sg-notes">{found.notes}</pre>}</div>}
      </div>
      <div className="card form">
        <h3>Корпоративный канал этого сервера</h3>
        <p className="help">Сотрудники получают только ту версию, которую вы опубликовали здесь. Сервер скачивает манифест и установщик издателя, проверяет подпись и SHA-256
          и раздаёт их как есть — клиент ещё раз проверяет подпись издателя, поэтому подменить обновление через этот сервер нельзя.</p>
        <dl className="sg-kv">
          <dt>Опубликовано</dt><dd>{ch.published ? <>версия <b>{ch.published.version}</b>, {fmtDate(ch.published.published_at)}, {ch.published.by}</> : "ничего"}</dd>
          {ch.published && <><dt>Установщик</dt><dd className="sg-fp small">{ch.published.file} · SHA-256 {ch.published.sha256}</dd></>}
          <dt>Адрес для клиентов</dt><dd className="sg-fp">{ch.client_url} <Copy text={ch.client_url} /></dd>
        </dl>
        <p className="help">Этот адрес задаётся политикой <code>UpdateUrl</code> (шаблон ADMX, свойство MSI <code>UPDATEURL</code>) или выбирается в корпоративном выпуске («Корпоративный канал этого сервера»).
          Установки MSI под управлением администратора сами не обновляются — их обновляют новым MSI через групповую политику.</p>
        <div className="row"><button className="btn primary" disabled={busy || !ch.pubkeys} onClick={() => {
          if (window.confirm("Опубликовать текущую версию издателя для сотрудников?")) void act(() => api.signing.channelPublish(), "Версия опубликована в корпоративном канале");
        }}>Опубликовать текущую версию издателя</button></div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------------------------- диагностика
function DiagTab({ ov }: { ov: SigningOverview }) {
  const failed = ov.jobs.filter((j) => j.status === "failed");
  const soon = ov.certs.filter((c) => !c.retired && c.days_left < 45);
  return (
    <>
      {!ov.agents.some((a) => a.online) && <div className="alert warn">Ни один подписывающий узел сейчас не на связи: задания будут ждать в очереди. Проверьте задачу «Peregovorka Signer» на узле.</div>}
      {soon.map((c) => <div key={c.id} className="alert warn">Сертификат «{cn(c.subject)}» {c.days_left < 0 ? "истёк" : `истекает через ${c.days_left} дн.`} — подготовьте замену (вкладка «Инструкции»).</div>)}
      {!ov.certs.some((c) => c.active) && ov.certs.length > 0 && <div className="alert warn">Активный сертификат не выбран: назначьте его на вкладке «Сертификаты».</div>}
      <div className="card form">
        <h3>Узлы</h3>
        {ov.agents.length === 0 ? <p className="muted">Нет подключённых узлов.</p> : (
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Узел</th><th>Система</th><th>PowerShell</th><th>signtool</th><th>Проверка издателя</th><th>Последняя связь</th></tr></thead>
            <tbody>{ov.agents.map((a) => (
              <tr key={a.id}><td>{a.name}</td><td className="small">{a.info.os ?? "—"}</td><td className="small">{a.info.powershell ?? "—"}</td>
                <td>{a.info.signtool === "True" ? <Badge kind="ok" text="есть" /> : <Badge kind="" text="нет (Set-AuthenticodeSignature)" />}</td>
                <td>{a.info.publisher_pin === "True" ? <Badge kind="ok" text="отпечаток закреплён" /> : <Badge kind="warn" text="только SHA-256" />}</td>
                <td className="small">{fmtDate(a.last_seen_at)}</td></tr>))}</tbody>
          </table></div>)}
      </div>
      <div className="card form">
        <h3>Неуспешные задания</h3>
        {failed.length === 0 ? <p className="muted">Ошибок нет.</p> : <JobsList jobs={failed} agents={ov.agents} busy act={async () => undefined} />}
      </div>
      <div className="card form">
        <h3>Проверка подписи на любом компьютере</h3>
        <Code>{"Get-AuthenticodeSignature .\\Peregovorka-Setup-1.0.1.exe | Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate"}</Code>
        <Code>{"signtool verify /pa /v .\\Peregovorka-1.0.1-x64.msi"}</Code>
        <p className="help"><b>Valid</b> — подпись цела и издатель доверен. <b>UnknownError / NotTrusted</b> — подпись цела, но компьютер не доверяет издателю (распространите сертификат). <b>HashMismatch</b> — файл изменён после подписи: не устанавливайте.</p>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------------------------- инструкции
function Guide({ server }: { server: string }) {
  return (
    <div className="sg-guide">
      <div className="alert info">Сертификат подписи издателя и ключ обновлений — разные вещи: первый подписывает установщики (Windows проверяет издателя), второй подписывает
        описание новых версий (приложение проверяет, что обновление настоящее). Ни то, ни другое не передаётся через браузер.</div>
      <section className="card form">
        <h3>1. Подписывающий узел</h3>
        <p>Выделенный компьютер Windows (рабочая станция или сервер), к которому имеют доступ только уполномоченные администраторы. Скопируйте на него <code>Peregovorka-Signer.ps1</code>,
          на вкладке «Сертификаты» нажмите «Подключить узел» и выполните показанные команды в PowerShell от имени администратора.</p>
        <Code>{`.\\Peregovorka-Signer.ps1 Enroll -Server ${server} -Code <код> -Name "Узел подписи" -PublisherThumbprint <отпечаток издателя>\n.\\Peregovorka-Signer.ps1 Install`}</Code>
        <p className="help"><code>-PublisherThumbprint</code> — необязательно: узел будет принимать исходные пакеты только с подписью этого издателя (в дополнение к сверке SHA-256).</p>
      </section>
      <section className="card form">
        <h3>2. Сертификат из корпоративного УЦ (Microsoft AD CS)</h3>
        <ol className="sg-steps">
          <li>Создайте или выберите шаблон сертификата с назначением <b>Code Signing</b> (например, копия шаблона «Подписывание кода»), ключ RSA 3072+ или ECDSA P-256, без экспорта ключа.</li>
          <li>Разрешите выпуск по шаблону (Enroll) только учётной записи подписывающего узла или группе уполномоченных администраторов; опубликуйте шаблон на УЦ.</li>
          <li>На подписывающем узле запросите сертификат (имя шаблона — пример, шаблон должен существовать в вашем AD CS; команду выполняет уполномоченный администратор):
            <Code>{"Get-Certificate `\n  -Template \"PeregovorkaCodeSigning\" `\n  -CertStoreLocation \"Cert:\\LocalMachine\\My\""}</Code></li>
          <li>Убедитесь, что у сертификата есть назначение «Подпись кода» и закрытый ключ:
            <Code>{"Get-ChildItem Cert:\\LocalMachine\\My |\nWhere-Object {\n    $_.HasPrivateKey -and\n    $_.EnhancedKeyUsageList.ObjectId -contains \"1.3.6.1.5.5.7.3.3\"\n} |\nSelect-Object Subject, Thumbprint, NotAfter"}</Code></li>
          <li>Зарегистрируйте его: «Сертификаты → Подключить сертификат компании», отпечаток <code>Thumbprint</code> из вывода выше. Затем «Проверить сертификат».</li>
          <li>Подпишите пробный выпуск («Корпоративные установщики») и проверьте подпись на компьютере сотрудника:
            <Code>{"Get-AuthenticodeSignature .\\Peregovorka-Setup-1.0.1-meet.example.org.exe | Format-List Status, SignerCertificate\nsigntool verify /pa /v .\\Peregovorka-1.0.1-meet.example.org-x64.msi"}</Code></li>
          <li>Распространите доверие групповой политикой (раздел 4). Закрытый ключ корневого УЦ для этого не нужен и не запрашивается.</li>
        </ol>
        <p className="help">Сертификат другого корпоративного УЦ — так же: нужен сертификат с назначением Code Signing и закрытым ключом на узле. Если сертификат выдан файлом PFX,
          импортируйте его на самом узле (пароль вводится там же, ключ становится неэкспортируемым), а файл затем удалите:</p>
        <Code>{".\\Peregovorka-Signer.ps1 ImportPfx -Path C:\\Temp\\codesign.pfx"}</Code>
      </section>
      <section className="card form">
        <h3>3. Собственный сертификат Peregovorka</h3>
        <p>«Сертификаты → Создать собственный сертификат» — узел создаёт самоподписанный сертификат подписи кода (RSA 3072, SHA-256, ключ не экспортируется). Подходит,
          если в организации нет своего УЦ. Компьютеры сотрудников ему <b>не доверяют</b> автоматически: Peregovorka не меняет доверенные корневые сертификаты сотрудников —
          это делает администратор групповой политикой (раздел 4), по открытому сертификату «Скачать публичный сертификат (.cer)».</p>
      </section>
      <section className="card form">
        <h3>4. Доверие через групповые политики (GPO)</h3>
        <p>Консоль «Управление групповой политикой» → политика для компьютеров сотрудников → <i>Конфигурация компьютера → Политики → Конфигурация Windows → Параметры безопасности →
          Политики открытого ключа</i>:</p>
        <ul className="sg-steps">
          <li><b>Доверенные корневые центры сертификации</b> → «Импорт…»: корневой сертификат УЦ организации (для собственного сертификата Peregovorka — его .cer).</li>
          <li><b>Промежуточные центры сертификации</b> → «Импорт…»: сертификаты промежуточных УЦ цепочки, если они есть.</li>
          <li><b>Доверенные издатели</b> → «Импорт…»: сертификат издателя (.cer со вкладки «Сертификаты») — тогда Windows не спрашивает подтверждения для подписанных им программ.</li>
        </ul>
        <p className="help">Проверка на компьютере после <code>gpupdate /force</code>:</p>
        <Code>{"Get-ChildItem Cert:\\LocalMachine\\Root, Cert:\\LocalMachine\\TrustedPublisher | Where-Object Thumbprint -eq \"<отпечаток>\""}</Code>
        <p className="help">Peregovorka не вносит изменения в групповые политики сама — все шаги выполняет администратор домена.</p>
      </section>
      <section className="card form">
        <h3>5. Развёртывание MSI</h3>
        <p>Корпоративный MSI уже содержит адрес сервера и настройки — его можно назначать групповой политикой («Конфигурация компьютера → Политики → Конфигурация программ →
          Установка программ → Создать → Пакет», путь — сетевой ресурс <code>\\\\сервер\\share\\…</code>), через SCCM/Intune или вручную:</p>
        <Code>{"msiexec /i Peregovorka-1.0.1-meet.example.org-x64.msi /qn"}</Code>
        <p className="help">Новая версия — новый корпоративный MSI той же политикой (обновление поверх сохраняет настройки и входы сотрудников). Шаблон ADMX для остальных параметров —
          <code>desktop/installer/admx</code> (docs/DESKTOP_ADMIN.md).</p>
      </section>
      <section className="card form">
        <h3>6. Замена сертификата</h3>
        <ul className="sg-steps">
          <li>Выпустите новый сертификат <b>на того же издателя</b> (то же имя субъекта) до истечения старого, зарегистрируйте его и нажмите «Проверить».</li>
          <li>Распространите новый сертификат в «Доверенные издатели» (и новый корень, если он сменился) — заранее, до первой сборки.</li>
          <li>«Сделать активным» — новые сборки подписываются им; уже установленные клиенты обновляются: приложение сверяет имя издателя, а не отпечаток.</li>
          <li>Подписи, сделанные с меткой времени, остаются действительными и после истечения старого сертификата.</li>
        </ul>
      </section>
    </div>
  );
}
