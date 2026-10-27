import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ApiError, type SecBlock, type SecEvent, type SecOverview } from "../../api";
import { ConfirmDialog, Modal } from "../../components/Dialogs";
import { useToast } from "../../components/Toast";
import { fmt } from "../../util";

const RESULT: Record<string, string> = { ok: "выполнено", denied: "отказ", blocked: "блокировка", observed: "замечено", passed: "пройдено", required: "требуется", failed: "не пройдено" };
const MODE: Record<string, string> = { off: "выключена", after_failures: "после серии неудач", always: "всегда", observe: "наблюдение", protect: "защита" };
const periodFrom = (days: number) => new Date(Date.now() - days * 86400_000).toISOString();
const until = (t: number) => new Date(t * 1000).toISOString();

/** «События и блокировки»: сводка состояния защиты, таблица событий безопасности с фильтрами и выгрузкой, вкладка «Заблокированные адреса». */
export default function SecurityEventsAdmin({ initialTab = "events" }: { initialTab?: "events" | "blocks" }) {
  const [tab, setTab] = useState<"events" | "blocks">(initialTab);
  const [ov, setOv] = useState<SecOverview | null>(null);
  const [toast, show] = useToast();
  const loadOv = useCallback(() => api.admin.secOverview().then(setOv).catch(() => undefined), []);
  useEffect(() => { void loadOv(); }, [loadOv, tab]);
  const on = (b: boolean) => <span className={`badge ${b ? "ok" : "warn"}`}>{b ? "включена" : "выключена"}</span>;
  return (
    <>
      {toast}
      <div className="card">
        <div className="row"><h2>Состояние защиты</h2><div className="spacer" /><button className="btn" onClick={() => void loadOv()}>Обновить</button></div>
        {ov && (
          <>
            <div className="sec-tiles">
              <Tile label="Заблокировано адресов" value={ov.blocked_now} warn={ov.blocked_now > 0} onClick={() => setTab("blocks")} />
              <Tile label="Неудачных входов за сутки" value={ov.failed_logins_24h} />
              <Tile label="Входов отклонено защитой" value={ov.login_blocked_24h} warn={ov.login_blocked_24h > 0} />
              <Tile label="CAPTCHA показана / пройдена" value={`${ov.captcha_shown_24h} / ${ov.captcha_passed_24h}`} />
              <Tile label="Отказов по подсетям" value={ov.denied_net_24h} />
              <Tile label="Срабатываний WAF" value={ov.waf_hits_24h} />
            </div>
            <p className="small" style={{ margin: "8px 0 2px" }}>
              Защита входа {on(ov.protection.login_protection)} · CAPTCHA: <b>{MODE[ov.protection.captcha_mode] ?? ov.protection.captcha_mode}</b> · ограничение по подсетям: <b>{ov.protection.ip_access.active ? `действует (${ov.protection.ip_access.networks})` : ov.protection.ip_access.enabled ? "включено, не действует" : "выключено"}</b> · WAF: <b>{MODE[ov.protection.waf_mode] ?? ov.protection.waf_mode}</b>
            </p>
            {ov.top_rules.length > 0 && <p className="muted small" style={{ margin: "2px 0" }}>Чаще всего за сутки срабатывали: {ov.top_rules.map((r) => `${r.rule} — ${r.count}`).join("; ")}.</p>}
            {ov.attention.map((a) => <div key={a} className="alert info small" role="status">⚠ {a}</div>)}
          </>
        )}
      </div>
      <div className="card">
        <div className="seg" role="tablist" aria-label="Раздел">
          <button role="tab" aria-pressed={tab === "events"} onClick={() => setTab("events")}>События</button>
          <button role="tab" aria-pressed={tab === "blocks"} onClick={() => setTab("blocks")}>Заблокированные адреса{ov && ov.blocked_now > 0 ? ` (${ov.blocked_now})` : ""}</button>
        </div>
        {tab === "events" ? <Events /> : <Blocks onChanged={() => { void loadOv(); }} show={show} />}
      </div>
    </>
  );
}

function Tile({ label, value, warn, onClick }: { label: string; value: number | string; warn?: boolean; onClick?: () => void }) {
  const inner = <><div className={`sec-tile-v${warn ? " warn" : ""}`}>{value}</div><div className="muted small">{label}</div></>;
  return onClick ? <button type="button" className="sec-tile" onClick={onClick}>{inner}</button> : <div className="sec-tile">{inner}</div>;
}

function Events() {
  const [f, setF] = useState({ days: 1, ip: "", account: "", kind: "", result: "", q: "" });
  const [rows, setRows] = useState<SecEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [kinds, setKinds] = useState<{ id: string; title: string }[]>([]);
  const [page, setPage] = useState(0);
  const [err, setErr] = useState("");
  const q = useMemo(() => ({ from: periodFrom(f.days), ip: f.ip.trim(), account: f.account.trim(), kind: f.kind, result: f.result, q: f.q.trim() }), [f]);
  const load = useCallback(() => api.admin.secEvents({ ...q, limit: 100, offset: page * 100 }).then((r) => { setRows(r.items); setTotal(r.total); setKinds(r.kinds); setErr(""); }).catch((e) => setErr((e as ApiError).message)), [q, page]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setPage(0); }, [q]);
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "flex-end", margin: "10px 0" }}>
        <label>Период<select value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })}><option value={1}>сутки</option><option value={7}>7 дней</option><option value={30}>30 дней</option><option value={365}>весь срок хранения</option></select></label>
        <label>IP-адрес<input value={f.ip} onChange={(e) => setF({ ...f, ip: e.target.value })} placeholder="198.51.100." style={{ width: 150 }} /></label>
        <label>Учётная запись<input value={f.account} onChange={(e) => setF({ ...f, account: e.target.value })} style={{ width: 140 }} /></label>
        <label>Тип события<select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="">все</option>{kinds.map((k) => <option key={k.id} value={k.id}>{k.title}</option>)}</select></label>
        <label>Результат<select value={f.result} onChange={(e) => setF({ ...f, result: e.target.value })}><option value="">любой</option><option value="denied">отказ</option><option value="blocked">блокировка</option><option value="observed">замечено (наблюдение)</option><option value="passed">пройдено</option><option value="required">требуется проверка</option><option value="failed">не пройдено</option><option value="ok">выполнено</option></select></label>
        <label style={{ flex: "1 1 160px" }}>Причина / правило<input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} /></label>
        <button className="btn" onClick={() => void load()}>Обновить</button>
        <a className="btn" href={api.admin.secEventsExportUrl(q, "csv")} download>Выгрузить CSV</a>
        <a className="btn" href={api.admin.secEventsExportUrl(q, "json")} download>JSON</a>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      <div style={{ overflowX: "auto" }}>
        <table className="table small">
          <thead><tr><th>Дата и время</th><th>IP-адрес</th><th>Учётная запись</th><th>Событие</th><th>Результат</th><th>Причина</th><th>Правило</th><th>Блокировка до</th><th>Компонент</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td className="nowrap">{fmt(e.at)}</td><td><code>{e.ip ?? "—"}</code></td><td>{e.account ?? "—"}</td><td>{e.kind_title}</td>
                <td><span className={`badge ${e.result === "blocked" || e.result === "denied" ? "warn" : e.result === "passed" || e.result === "ok" ? "ok" : ""}`}>{RESULT[e.result] ?? (e.result || "—")}</span></td>
                <td>{e.reason ?? "—"}</td><td><code>{e.rule ?? "—"}</code></td><td className="nowrap">{e.until ? fmt(e.until) : "—"}</td><td>{e.component || "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={9} className="muted">Событий за выбранный период нет.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <span className="muted small">Найдено: {total}. Пароли, токены, гостевые ссылки и cookie в журнал не попадают; срок хранения и размер — в разделе «Хранение журнала событий безопасности».</span>
        <div className="spacer" />
        <button className="btn mini" disabled={page === 0} onClick={() => setPage(page - 1)}>← Назад</button>
        <button className="btn mini" disabled={(page + 1) * 100 >= total} onClick={() => setPage(page + 1)}>Далее →</button>
      </div>
    </>
  );
}

function Blocks({ onChanged, show }: { onChanged: () => void; show: (t: string, k?: "ok" | "error") => void }) {
  const [items, setItems] = useState<SecBlock[]>([]);
  const [err, setErr] = useState("");
  const [unblock, setUnblock] = useState<SecBlock | null>(null);
  const [view, setView] = useState<{ ip: string; items: SecEvent[] } | null>(null);
  const [form, setForm] = useState({ ip: "", minutes: 60, reason: "" });
  const [shared, setShared] = useState<string | null>(null);
  const load = useCallback(() => api.admin.secBlocks().then((r) => { setItems(r.items); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const add = async (confirmShared = false) => {
    try { await api.admin.secBlock({ ip: form.ip.trim(), minutes: form.minutes, reason: form.reason.trim(), confirm_shared: confirmShared }); setForm({ ip: "", minutes: 60, reason: "" }); setShared(null); show("Адрес заблокирован", "ok"); await load(); onChanged(); }
    catch (e) { const a = e as ApiError; if (a.status === 409 && a.code === "shared_address") setShared(a.message); else setErr(a.message); }
  };
  return (
    <>
      {err && <div className="alert error" role="alert">{err}</div>}
      <p className="muted small" style={{ margin: "10px 0 6px" }}>Блокировка — временная, срок всегда ограничен. Она закрывает вход и анонимные обращения с адреса; уже вошедшие пользователи не отключаются. Автоматически блокируются только адреса, не похожие на общий NAT.</p>
      <div style={{ overflowX: "auto" }}>
        <table className="table small">
          <thead><tr><th>IP-адрес</th><th>Причина</th><th>Неудач (24 ч)</th><th>Начало</th><th>Окончание</th><th>Правила</th><th>Последние события</th><th /></tr></thead>
          <tbody>
            {items.map((b) => (
              <tr key={b.ip}>
                <td><code>{b.ip}</code>{b.manual && <div className="muted small">вручную: {b.actor}</div>}</td>
                <td>{b.reason}</td><td>{b.failures_24h}</td><td className="nowrap">{fmt(until(b.since))}</td><td className="nowrap">{fmt(until(b.until))}</td>
                <td><code>{b.rule}</code>{b.level > 1 && <div className="muted small">повтор №{b.level}</div>}</td>
                <td>{b.recent.slice(0, 3).map((r, i) => <div key={i} className="muted small">{fmt(r.at)} · {r.kind_title}</div>)}</td>
                <td className="nowrap"><button className="btn mini" onClick={() => void api.admin.secActivity(b.ip.split("/")[0]).then((r) => setView({ ip: b.ip, items: r.items })).catch((e) => setErr((e as ApiError).message))}>Посмотреть активность</button>{" "}
                  <button className="btn mini" onClick={() => setUnblock(b)}>Снять блокировку</button></td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={8} className="muted">Сейчас заблокированных адресов нет.</td></tr>}
          </tbody>
        </table>
      </div>
      <h3 style={{ marginTop: 16 }}>Заблокировать вручную</h3>
      <form className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); void add(); }}>
        <label>IP-адрес<input value={form.ip} onChange={(e) => setForm({ ...form, ip: e.target.value })} placeholder="203.0.113.50" autoComplete="off" style={{ width: 170 }} /></label>
        <label>Срок<select value={form.minutes} onChange={(e) => setForm({ ...form, minutes: Number(e.target.value) })}><option value={15}>15 минут</option><option value={60}>1 час</option><option value={360}>6 часов</option><option value={1440}>сутки</option><option value={10080}>7 суток (максимум)</option></select></label>
        <label style={{ flex: "1 1 240px" }}>Причина<input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Подбор паролей, сообщение службы ИБ №…" maxLength={200} /></label>
        <button className="btn primary" type="submit" disabled={!form.ip.trim() || form.reason.trim().length < 3}>Заблокировать</button>
      </form>
      {unblock && (
        <ConfirmDialog title="Снять блокировку?" confirmLabel="Снять блокировку" danger={false} onClose={() => setUnblock(null)}
          body={<p>Адрес <code>{unblock.ip}</code> сможет снова обращаться к серверу. Счётчики неудач и история повторных блокировок останутся: если подбор продолжится, адрес будет заблокирован снова, причём на более долгий срок.</p>}
          onConfirm={async () => { await api.admin.secUnblock(unblock.ip); show("Блокировка снята", "ok"); await load(); onChanged(); }} />
      )}
      {shared && <ConfirmDialog title="Общий адрес" confirmLabel="Всё равно заблокировать" onClose={() => setShared(null)} body={<p>{shared}</p>} onConfirm={async () => { await add(true); }} />}
      {view && (
        <Modal title={`Активность ${view.ip}`} onClose={() => setView(null)} wide noAutofocus>
          <p className="muted small">Хронология за двое суток (новые сверху) и объяснение сработавших правил.</p>
          <div style={{ overflow: "auto", maxHeight: "60vh" }}>
            <table className="table small"><thead><tr><th>Время</th><th>Событие</th><th>Причина / правило</th></tr></thead><tbody>
              {view.items.map((e) => <tr key={e.id}><td className="nowrap">{fmt(e.at)}</td><td>{e.kind_title}{e.account ? ` · ${e.account}` : ""}</td><td>{e.reason ?? ""} {e.rule && <code>{e.rule}</code>}{e.explain && <div className="muted small">{e.explain}</div>}</td></tr>)}
              {view.items.length === 0 && <tr><td colSpan={3} className="muted">Событий нет.</td></tr>}
            </tbody></table>
          </div>
        </Modal>
      )}
    </>
  );
}
