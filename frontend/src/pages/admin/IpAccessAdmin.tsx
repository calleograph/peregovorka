import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type IpAccessEntry, type IpAccessState } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";
import { useToast } from "../../components/Toast";
import { fmt } from "../../util";

const left = (s: number | null) => (s == null ? "" : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);

/** Администрирование → Безопасность → Доверенные подсети. Ограничение доступа по IP с защитой администратора от самоизоляции:
 *  проверка списка и адреса, пробный срок с подтверждением и автооткатом, независимая консольная команда восстановления. */
export default function IpAccessAdmin() {
  const [st, setSt] = useState<IpAccessState | null>(null);
  const [err, setErr] = useState("");
  const [cidr, setCidr] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [risk, setRisk] = useState<{ text: string; run: () => Promise<unknown> } | null>(null);
  const [del, setDel] = useState<IpAccessEntry | null>(null);
  const [toast, show] = useToast();
  const load = useCallback(() => api.admin.ipAccess().then((s) => { setSt(s); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (!st?.pending) return; const t = window.setInterval(() => void load(), 1000); return () => window.clearInterval(t); }, [st?.pending, load]);

  // изменение, которое отрезает самого администратора, требует явного подтверждения (ответ 409 admin_not_allowed)
  const guarded = async (act: (ack: boolean) => Promise<IpAccessState>, okText?: string) => {
    setBusy(true); setErr("");
    try { setSt(await act(false)); if (okText) show(okText, "ok"); }
    catch (e) {
      const a = e as ApiError;
      if (a.status === 409 && a.code === "admin_not_allowed") setRisk({ text: a.message, run: async () => { setSt(await act(true)); if (okText) show(okText, "ok"); } });
      else setErr(a.message);
    } finally { setBusy(false); }
  };

  const toggle = (on: boolean) => guarded((ack) => api.admin.ipAccessSettings({ enabled: on, acknowledge_lockout: ack }), on ? "Ограничение включено. Подтвердите его из разрешённой сети." : "Ограничение выключено.");
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!cidr.trim()) return;
    setBusy(true); setErr("");
    try { setSt(await api.admin.ipAccessAdd({ cidr: cidr.trim(), comment: comment.trim() })); setCidr(""); setComment(""); } catch (x) { setErr((x as ApiError).message); } finally { setBusy(false); }
  };
  const addMine = () => { if (st) { setCidr(st.my_ip); setComment("Адрес администратора"); } };

  if (!st) return <div className="card">{err ? <div className="alert error" role="alert">{err}</div> : <p className="muted">Загрузка…</p>}</div>;
  const nEnabled = st.entries.filter((x) => x.enabled).length;
  return (
    <>
      {toast}
      <div className="card">
        <div className="row"><h2>Доверенные подсети</h2>
          <span className={`badge ${st.active ? "ok" : ""}`}>{st.active ? "ограничение действует" : st.enabled ? "включено, но не действует" : "выключено"}</span>
          {st.pending && <span className="badge warn">ждёт подтверждения {left(st.seconds_left)}</span>}
          <div className="spacer" />
          <span className="muted small">Отказов за 24 часа: <strong>{st.denied_24h}</strong></span>
        </div>
        <p className="muted small" style={{ margin: "4px 0 10px" }}>Если ограничение включено, к Peregovorka можно обращаться только с перечисленных адресов и подсетей. Это <b>не то же самое</b>, что «доверенные прокси»: те сообщают серверу настоящий адрес клиента (раздел «Сеть и IP-адреса»), а здесь решается, какие сети допущены. По умолчанию ограничение выключено.</p>
        {err && <div className="alert error" role="alert">{err}</div>}
        {st.warnings.map((w) => <div key={w} className="alert info small" role="status">⚠ {w}</div>)}
        {st.pending && (
          <div className="alert info" role="status">
            <strong>Подтвердите настройку.</strong> Ограничение уже действует. Если вы видите эту страницу — ваш адрес {st.my_ip} допущен. Нажмите «Подтвердить»; иначе через {left(st.seconds_left)} ограничение выключится само (на случай, если вы потеряли доступ).
            <div className="row" style={{ marginTop: 6 }}><button className="btn primary" disabled={busy} onClick={() => void api.admin.ipAccessConfirm().then((s) => { setSt(s); show("Настройка подтверждена", "ok"); }).catch((e) => setErr((e as ApiError).message))}>Подтвердить</button></div>
          </div>
        )}
        <p style={{ margin: "6px 0" }}>Ваш адрес для сервера: <strong>{st.my_ip}</strong>{" "}
          <span className={`badge ${st.my_ip_allowed ? "ok" : "warn"}`}>{st.my_ip_allowed ? "входит в разрешённые" : "не входит в разрешённые"}</span></p>
        <label className="check" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={st.enabled} disabled={busy || st.forced_off} onChange={(e) => void toggle(e.target.checked)} />
          <span>Ограничить доступ к Peregovorka разрешёнными IP-адресами и подсетями</span>
        </label>
        <p className="muted small" style={{ margin: "4px 0 0" }}>При включении запускается пробный срок {st.trial_minutes} мин: вы должны подтвердить настройку из разрешённой сети, иначе ограничение выключится само. Аварийное выключение с консоли сервера: <code>scripts/security-reset.sh</code>.</p>
        <div className="row" style={{ marginTop: 10, flexWrap: "wrap", gap: 16 }}>
          <label className="check"><input type="checkbox" checked={st.exempt_guests} disabled={busy} onChange={(e) => void api.admin.ipAccessSettings({ exempt_guests: e.target.checked }).then(setSt).catch((x) => setErr((x as ApiError).message))} /> <span>Не ограничивать гостевые ссылки (гости приходят из любых сетей)</span></label>
          <label className="check"><input type="checkbox" checked={st.exempt_public_api} disabled={busy} onChange={(e) => void api.admin.ipAccessSettings({ exempt_public_api: e.target.checked }).then(setSt).catch((x) => setErr((x as ApiError).message))} /> <span>Не ограничивать публичный API (у ключей свои списки адресов)</span></label>
        </div>
      </div>

      <div className="card">
        <h2>Разрешённые адреса и подсети</h2>
        <form className="row" onSubmit={add} style={{ alignItems: "flex-end", flexWrap: "wrap", gap: 8 }}>
          <label style={{ flex: "1 1 220px" }}>IP-адрес или подсеть<input value={cidr} onChange={(e) => setCidr(e.target.value)} placeholder="198.51.100.0/24 или 192.0.2.10 или 2001:db8::/32" autoComplete="off" spellCheck={false} /></label>
          <label style={{ flex: "2 1 260px" }}>Комментарий<input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Офис, VPN, филиал…" maxLength={300} /></label>
          <button className="btn primary" type="submit" disabled={busy || !cidr.trim()}>Добавить</button>
          <button className="btn" type="button" onClick={addMine} title="Подставить адрес, с которого вы сейчас работаете">Мой адрес</button>
        </form>
        <div style={{ overflowX: "auto", marginTop: 8 }}>
          <table className="table small">
            <thead><tr><th>Включена</th><th>Адрес / подсеть</th><th>Комментарий</th><th>Изменено</th><th /></tr></thead>
            <tbody>
              {st.entries.map((r) => (
                <tr key={r.id}>
                  <td><input type="checkbox" aria-label={`Включить ${r.cidr}`} checked={r.enabled} disabled={busy} onChange={(e) => void guarded((ack) => api.admin.ipAccessUpdate(r.id, { enabled: e.target.checked, acknowledge_lockout: ack }))} /></td>
                  <td><code>{r.cidr}</code></td>
                  <td>{r.comment || <span className="muted">—</span>}</td>
                  <td className="muted">{fmt(r.updated_at)}<br />{r.updated_by}</td>
                  <td><button className="btn ghost mini danger" disabled={busy} onClick={() => setDel(r)}>Удалить</button></td>
                </tr>
              ))}
              {st.entries.length === 0 && <tr><td colSpan={5} className="muted">Список пуст. Пока в нём нет ни одной включённой записи, ограничение включить нельзя.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="muted small">Включено записей: {nEnabled}. Поддерживаются отдельные IPv4/IPv6-адреса и подсети в формате CIDR.</p>
      </div>

      <div className="card">
        <h2>Что охватывает ограничение</h2>
        <p className="muted small" style={{ margin: "0 0 8px" }}>Встроенный фильтр действует на трафик, который проходит через backend приложения. Он <b>не заменяет межсетевой экран</b> и не закрывает порты сервера.</p>
        <div className="sec-grid">
          <div><h3 style={{ fontSize: 14 }}>Охвачено</h3><ul className="small">{st.covered.map((c) => <li key={c.id}><b>{c.title}.</b> {c.detail}</li>)}</ul></div>
          <div><h3 style={{ fontSize: 14 }}>Не охвачено — настройте внешний firewall/WAF</h3><ul className="small">{st.not_covered.map((c) => <li key={c.id}><b>{c.title}.</b> {c.detail}</li>)}</ul></div>
        </div>
      </div>

      {risk && (
        <ConfirmDialog title="Вы можете потерять доступ" confirmLabel="Понимаю риск, продолжить" onClose={() => setRisk(null)} body={<p>{risk.text} Пробный срок {st.trial_minutes} мин: если не подтвердить настройку из разрешённой сети, ограничение выключится само.</p>}
          onConfirm={async () => { await risk.run(); }} />
      )}
      {del && (
        <ConfirmDialog title="Удалить запись?" confirmLabel="Удалить" onClose={() => setDel(null)} body={<p>Запись <code>{del.cidr}</code> будет удалена из списка разрешённых сетей.</p>}
          onConfirm={async () => { await new Promise<void>((resolve, reject) => { void guarded((ack) => api.admin.ipAccessDelete(del.id, { acknowledge_lockout: ack }), "Запись удалена").then(() => resolve(), reject); }); }} />
      )}
    </>
  );
}
