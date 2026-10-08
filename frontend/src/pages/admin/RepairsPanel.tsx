import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type RepairItem, type RepairsInfo } from "../../api";

/**
 * «Исправить автоматически»: известные проблемы установки в виде «Проблема → Что это значит → Что будет сделано → кнопка». Команд Linux пользователь не видит
 * (единственное исключение — единоразовая установка самого помощника обновлений: без него из браузера нечем исправлять). Исправление выполняет помощник на
 * сервере из фиксированного белого списка действий; после исправления проверка повторяется автоматически, и пункт исчезает, если проблема решена.
 */
export default function RepairsPanel({ onOpen, quiet = false }: { onOpen?: (page: string) => void; quiet?: boolean }) {
  const [info, setInfo] = useState<RepairsInfo | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [working, setWorking] = useState("");   // id исправления, которое сейчас выполняется
  const wasWorking = useRef("");

  const load = useCallback(async () => {
    try { setInfo(await api.admin.repairs()); setErr(""); } catch (e) { setErr((e as ApiError).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const busy = !!working || !!info?.busy;
  useEffect(() => {
    const t = window.setInterval(() => void load(), busy ? 2000 : 20000);
    return () => window.clearInterval(t);
  }, [busy, load]);

  // выполнение закончилось на сервере → сообщить итог (повторная проверка уже выполнена помощником)
  useEffect(() => {
    if (!working || !info) return;
    const cur = info.current;
    if (cur && cur.repair_id === working && cur.state === "idle" && cur.finished_at) {
      setMsg(cur.result === "ok" ? { ok: true, text: "Исправлено. Проверка повторена." } : { ok: false, text: "Исправить не удалось — подробности в журнале на странице «Обновления и версии». Остальная система не затронута." });
      wasWorking.current = working; setWorking("");
    }
  }, [info, working]);

  const fix = async (it: RepairItem) => {
    setMsg(null); setErr("");
    try {
      const r = await api.admin.repairFix(it.id);
      if (r.done) { setMsg({ ok: true, text: it.kind === "backend" ? "Настройки перенесены, подключение проверено." : "Готово." }); await load(); return; }
      setWorking(it.id);
      window.setTimeout(() => void load(), 1200);
    } catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); await load(); }
  };

  if (err && !info) return quiet ? null : <div className="alert error">{err}</div>;
  if (!info) return null;
  const items = info.items;
  if (!items.length && !msg && !working) return quiet ? null : <div className="alert ok repairs-ok">Известных проблем установки не найдено{info.age_s != null ? ` (проверено ${info.age_s < 90 ? `${info.age_s} с` : `${Math.round(info.age_s / 60)} мин`} назад)` : ""}.</div>;

  return (
    <div className="card repairs" aria-label="Исправить автоматически">
      <div className="row"><h3 style={{ margin: 0 }}>Найденные проблемы{items.length ? `: ${items.length}` : ""}</h3><div className="spacer" />
        <button className="btn mini" disabled={busy || !info.helper.available} onClick={() => api.admin.repairsScan().then(() => window.setTimeout(() => void load(), 2500)).catch((e) => setMsg({ ok: false, text: (e as ApiError).message }))}>Проверить заново</button></div>
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
      {items.map((it) => (
        <div key={it.id} className={`repair-item ${it.kind === "manual" ? "manual" : ""}`}>
          <div><b>Проблема:</b> {it.title}</div>
          <div className="small"><b>Что это значит:</b> {it.meaning}</div>
          <div className="small"><b>{it.kind === "manual" ? "Что нужно сделать" : "Что будет сделано"}:</b> {it.fix}</div>
          {it.command && <pre className="cmd">{it.command}</pre>}
          {it.kind !== "manual" && (
            <div className="row" style={{ marginTop: 6 }}>
              <button className="btn primary" disabled={busy || !it.fixable} onClick={() => void fix(it)}
                      title={it.fixable ? undefined : "Нужен помощник обновлений с правами администратора сервера"}>
                {working === it.id ? "Исправляю…" : it.kind === "backend" ? "Перенести настройки" : "Исправить автоматически"}</button>
              {it.id === "ldap_legacy" && onOpen && <button className="btn" onClick={() => onOpen("ldap")}>Открыть настройки LDAP</button>}
              {working === it.id && <span className="muted small">Выполняется на сервере, это может занять до нескольких минут. Страницу можно не закрывать.</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
