import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type ComponentOp, type ComponentsInfo, type MediaCheck, type ServerComponent } from "../../api";
import { Modal } from "../../components/Dialogs";
import InfoTip from "../../components/InfoTip";

export const WIKI = "https://github.com/calleograph/peregovorka/wiki/";
const STATE: Record<ServerComponent["state"], [string, string]> = {
  ok: ["работает", "ok"], off: ["выключена", ""], error: ["ошибка", "warn"], starting: ["запускается", ""], unknown: ["нет сведений", ""],
};

type Pending = { component: ServerComponent["id"]; op: Pick<ComponentOp, "id" | "label" | "title" | "consequences" | "interrupts_calls" | "danger"> };

/**
 * Состояние дополнительных компонентов сервера и действия с ними (включить видеоподсистему, перезапустить Egress…). Действие выполняет помощник
 * обновлений на хосте по белому списку; перед запуском — окно с последствиями, во время — этап и журнал, после — итог.
 */
export function useComponents() {
  const [info, setInfo] = useState<ComponentsInfo | null>(null);
  const [err, setErr] = useState("");
  const [log, setLog] = useState("");
  const offset = useRef(0);
  const watching = useRef(false);
  const load = useCallback(async () => {
    try { const i = await api.adminComponents(); setInfo(i); setErr(""); return i; } catch (e) { setErr((e as ApiError).message); return null; }
  }, []);
  useEffect(() => {
    let alive = true;
    let t: number | undefined;
    const tick = async () => {
      const i = await load();
      if (!alive) return;
      const busy = !!i?.helper.busy;
      if (busy || watching.current) {
        try {
          const l = await api.admin.updatesLog(offset.current);
          if (alive) { setLog((x) => (l.reset ? l.text : x + l.text).slice(-20000)); offset.current = l.offset; }
        } catch { /* журнал недоступен — пропускаем */ }
      }
      if (!busy && watching.current && i?.helper.last && !i.helper.last.running) watching.current = false;
      t = window.setTimeout(tick, busy || watching.current ? 2000 : 30000);
    };
    void tick();
    return () => { alive = false; window.clearTimeout(t); };
  }, [load]);
  const run = async (p: Pending) => {
    setLog(""); offset.current = -1; watching.current = true;
    await api.adminComponentRun(p.component, p.op.id);
    await load();
  };
  const refresh = async () => { try { await api.adminComponentsRefresh(); window.setTimeout(() => void load(), 2500); } catch (e) { setErr((e as ApiError).message); } };
  return { info, err, log, load, run, refresh };
}

/** Окно подтверждения: что произойдёт. Без подтверждения действие не отправляется (и сервер его не примет). */
export function ConfirmOp({ pending, onCancel, onRun }: { pending: Pending; onCancel: () => void; onRun: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <Modal title={pending.op.title} onClose={onCancel} noAutofocus>
      <p style={{ marginTop: 0 }}>Что произойдёт:</p>
      <ul className="consequences">{pending.op.consequences.map((c) => <li key={c}>{c}</li>)}</ul>
      {pending.op.interrupts_calls && <div className="alert warn small" role="alert">Идущие звонки прервутся на несколько секунд. Если сейчас идут важные встречи, выполните действие позже.</div>}
      {err && <div className="alert error small" role="alert">{err}</div>}
      <div className="row form-actions">
        <button type="button" className={`btn ${pending.op.danger ? "danger" : "primary"}`} disabled={busy}
                onClick={async () => { setBusy(true); setErr(""); try { await onRun(); } catch (e) { setErr((e as ApiError).message); setBusy(false); } }}>{busy ? "Отправка…" : pending.op.label}</button>
        <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Отмена</button>
      </div>
    </Modal>
  );
}

/** Карточка компонента: состояние, действия, ход и итог последнего действия. */
export function ComponentCard({ name, ctl }: { name: ServerComponent["id"]; ctl: ReturnType<typeof useComponents> }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const c = ctl.info?.items.find((x) => x.id === name);
  const h = ctl.info?.helper;
  const last = h?.last && h.last.op.startsWith(name === "video" ? "video" : name) ? h.last : null;
  if (!c) return ctl.err ? <div className="alert warn small" role="alert">{ctl.err}</div> : <p className="muted small">Загрузка сведений о службе…</p>;
  const [label, cls] = STATE[c.state];
  const canAct = !!h?.available && !!h.privileged && !h.busy;
  return (
    <div className="comp-card">
      <div className="comp-head">
        <b>{c.title}</b><InfoTip doc={c.doc}>{c.what}</InfoTip>
        <span className={`badge ${cls}`}>{label}</span>
        <div className="spacer" />
        {c.ops.map((o) => <button key={o.id} type="button" className={`btn mini ${o.primary ? "primary" : o.danger ? "danger" : ""}`} disabled={!canAct} onClick={() => setPending({ component: c.id, op: o })}>{o.label}</button>)}
        <button type="button" className="btn mini ghost" onClick={() => void ctl.refresh()} disabled={!h?.available || h.busy} title="Опросить службы на сервере">Обновить</button>
      </div>
      <span className="small">{c.message}</span>
      {h && !h.available && <div className="alert warn small" role="alert">Действия недоступны: {h.message} <a href={`${WIKI}Решение-проблем-обновления`} target="_blank" rel="noopener noreferrer">Как исправить</a></div>}
      {h?.available && !h.privileged && <div className="alert warn small" role="alert">{h.message}</div>}
      {h?.busy && !last?.running && <span className="muted small">Помощник занят другим действием — кнопки станут доступны после его завершения.</span>}
      {last && (
        <div className={`alert small ${last.running ? "" : last.result === "ok" ? "ok" : "error"}`} role="status">
          {last.running ? `Выполняется: ${last.title}…` : last.result === "ok" ? `Готово: ${last.title}.` : `Не удалось: ${last.title}. Причина — в журнале ниже.`}
          {ctl.log && <pre className="comp-log">{ctl.log}</pre>}
        </div>
      )}
      {pending && <ConfirmOp pending={pending} onCancel={() => setPending(null)} onRun={async () => { await ctl.run(pending); setPending(null); }} />}
    </div>
  );
}

const MARK: Record<MediaCheck["status"], string> = { ok: "✓", error: "✗", off: "○", skipped: "…", info: "ⓘ" };

/** Проверки по цепочке: у ошибок и выключенных возможностей — действие и ссылка на решение. */
export function ChecksList({ checks, ctl, onRepair }: { checks: MediaCheck[]; ctl: ReturnType<typeof useComponents>; onRepair?: (id: string) => Promise<void> }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [note, setNote] = useState("");
  const canAct = !!ctl.info?.helper.available && !!ctl.info.helper.privileged && !ctl.info.helper.busy;
  return (
    <>
      <ul className="checks-list" aria-label="Проверки">
        {checks.map((c) => (
          <li key={c.id} className={c.status}>
            <span className="ck-mark" aria-label={c.status}>{MARK[c.status]}</span>
            <span><b>{c.title}.</b> {c.message}{c.doc && c.status !== "ok" && <> <a href={c.doc} target="_blank" rel="noopener noreferrer">Решение</a></>}</span>
            <span className="ck-actions">
              {c.action?.kind === "component" && (() => { const a = c.action; return <button type="button" className="btn mini primary" disabled={!canAct}
                onClick={() => setPending({ component: a.component, op: { id: a.op, label: a.label, title: a.title, consequences: a.consequences, interrupts_calls: a.interrupts_calls, danger: a.danger } })}>{a.label}</button>; })()}
              {c.action?.kind === "repair" && onRepair && (() => { const a = c.action; return <button type="button" className="btn mini" disabled={!canAct}
                onClick={async () => { setNote(""); try { await onRepair(a.repair); setNote("Исправление запущено — ход виден в «Обновления и версии»."); } catch (e) { setNote((e as ApiError).message); } }}>{a.label}</button>; })()}
              {c.action?.kind === "anchor" && (() => { const a = c.action; return <button type="button" className="btn mini" onClick={() => document.getElementById(a.target)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{a.label}</button>; })()}
            </span>
          </li>
        ))}
      </ul>
      {note && <p className="muted small" role="status">{note}</p>}
      {pending && <ConfirmOp pending={pending} onCancel={() => setPending(null)} onRun={async () => { await ctl.run(pending); setPending(null); }} />}
    </>
  );
}
