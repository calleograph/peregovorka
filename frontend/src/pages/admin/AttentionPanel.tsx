import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type AttentionInfo, type AttentionItem } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";

const LEVEL: Record<AttentionItem["level"], [string, string]> = { critical: ["Критично", "err"], warning: ["Внимание", "warn"], info: ["К сведению", ""] };

/**
 * Центр внимания «Обзора»: что в системе требует действия администратора — из всех модулей (backend: services/attention.py). У каждого пункта:
 * что произошло, что затронуто, как влияет, что сделать, переход прямо к настройке; безопасное исправление — с описанием и подтверждением,
 * после него проверка повторяется сама. Список обновляется раз в минуту и по кнопке «Проверить сейчас».
 */
export default function AttentionPanel({ onOpen }: { onOpen: (tab: string, anchor?: string | null) => void }) {
  const [info, setInfo] = useState<AttentionInfo | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [fixing, setFixing] = useState<AttentionItem | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(async (refresh = false) => {
    if (refresh) setBusy(true);
    try { setInfo(await api.admin.attention(refresh)); setErr(""); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 60000); return () => window.clearInterval(t); }, [load]);

  const fix = async (it: AttentionItem) => {
    if (!it.fix) return;
    await api.admin.repairFix(it.fix.repair);
    setNote({ ok: true, text: `«${it.fix.label}»: выполняется на сервере. Проверка повторится автоматически.` });
    window.setTimeout(() => void load(true), 4000);
    window.setTimeout(() => void load(true), 15000);
  };

  const items = info?.items ?? [];
  return (
    <section className="card attention" aria-labelledby="attn-h">
      <div className="row attention-head">
        <h3 id="attn-h" className="field-title">Требует внимания{info && items.length > 0 && <span className="muted"> · {items.length}</span>}</h3>
        {info && (["critical", "warning", "info"] as const).filter((l) => info.counts[l]).map((l) => <span key={l} className={`badge ${LEVEL[l][1]}`}>{LEVEL[l][0]}: {info.counts[l]}</span>)}
        <div className="spacer" />
        {info && <span className="muted small">проверено {new Date(info.checked_at * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</span>}
        <button type="button" className="btn mini" onClick={() => void load(true)} disabled={busy}>{busy ? "Проверка…" : "Проверить сейчас"}</button>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {note && <div className={`alert ${note.ok ? "ok" : "error"}`} role="status">{note.text}</div>}
      {!info && !err && <p className="muted small">Проверка состояния…</p>}
      {info && items.length === 0 && <p className="attention-ok" role="status">✓ Всё в порядке: состояний, требующих действия, нет.</p>}
      {items.length > 0 && (
        <ul className="attention-list">
          {items.map((it) => (
            <li key={it.id} className={`attention-item lv-${it.level}`}>
              <div className="attention-top">
                <span className={`badge ${LEVEL[it.level][1]}`}>{LEVEL[it.level][0]}</span>
                <span className="muted small">{it.component}</span>
              </div>
              <b className="attention-title">{it.title}</b>
              <dl className="attention-dl">
                <dt>Влияние</dt><dd>{it.impact}</dd>
                <dt>Что сделать</dt><dd>{it.action}</dd>
              </dl>
              <div className="row attention-actions">
                <button type="button" className="btn mini primary" onClick={() => onOpen(it.tab, it.anchor)}>Перейти к настройке</button>
                {it.fix && <button type="button" className="btn mini" onClick={() => { setNote(null); setFixing(it); }}>{it.fix.label}</button>}
                {it.doc && <a className="btn mini ghost" href={it.doc} target="_blank" rel="noopener noreferrer">Как решить</a>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {fixing?.fix && <ConfirmDialog title={fixing.fix.label} danger={false} confirmLabel="Выполнить" onClose={() => setFixing(null)} onConfirm={() => fix(fixing)}
        body={<><p><b>{fixing.title}</b></p><p>{fixing.fix.confirm}</p></>} />}
    </section>
  );
}
