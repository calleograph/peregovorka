import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type SetupCheck, type SetupStatus } from "../../api";
import SetupWizard, { CHECK_BADGE, CHECK_LABEL } from "../../components/SetupWizard";
import { fmt } from "../../util";

/** Итог чек-листа одной строкой: «Готово 9 из 14 · требуют внимания: 5». */
export function summaryLine(st: Pick<SetupStatus, "summary" | "last_check_at">): string {
  const s = st.summary;
  return `Готово ${s.ok} из ${s.total} · требуют внимания: ${s.attention}`;
}

/** Проблемные пункты проверки (внимание и сбой — вперёд, затем «не настроено»), не больше `limit`. */
export function problemChecks(checks: SetupCheck[], limit = 6): SetupCheck[] {
  const rank = { fail: 0, warn: 1, todo: 2, ok: 3 } as const;
  return checks.filter((c) => c.status !== "ok").sort((a, b) => rank[a.status] - rank[b.status]).slice(0, limit);
}

/**
 * «Первоначальная настройка и проверка сервера» в «Состояние → Обзор»: всегда на месте, после закрытия мастера и после любых обновлений.
 * Показывает сколько готово и что требует внимания, когда проверяли в последний раз, позволяет открыть чек-лист и перепроверить сервер.
 */
export default function SetupChecklistCard({ onOpen }: { onOpen?: (page: string) => void }) {
  const [st, setSt] = useState<SetupStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.setupStatus().then(setSt).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const recheck = async () => { setBusy(true); setErr(""); try { setSt(await api.admin.setupCheck()); } catch (e) { setErr((e as ApiError).message); } setBusy(false); };
  if (!st) return err ? <div className="alert error" role="alert">{err}</div> : null;
  const problems = problemChecks(st.checks);
  return (
    <section className="card setup-card" aria-labelledby="setup-card-h">
      <div className="row">
        <h3 id="setup-card-h" style={{ margin: 0 }}>Первоначальная настройка и проверка сервера</h3>
        <span className={`badge ${st.summary.attention ? "warn" : "ok"}`}>{summaryLine(st)}</span>
        <div className="spacer" />
        <button className="btn" onClick={() => setOpen(true)}>Открыть чек-лист</button>
        <button className="btn primary" onClick={() => void recheck()} disabled={busy}>{busy ? "Проверяем…" : "Проверить повторно"}</button>
      </div>
      <p className="muted small" style={{ margin: "6px 0 0" }}>{st.last_check_at ? `Последняя проверка: ${fmt(st.last_check_at)}` : "Проверка состояния ещё не выполнялась — нажмите «Проверить повторно»."}</p>
      {err && <div className="alert error" role="alert" style={{ marginTop: 8 }}>{err}</div>}
      {problems.length > 0 && (
        <ul className="wizard-checks" style={{ marginTop: 8 }}>
          {problems.map((c) => (
            <li key={c.id}>
              <span className={`badge ${CHECK_BADGE[c.status] ?? ""}`}>{CHECK_LABEL[c.status] ?? c.status}</span>
              <div className="wz-body"><b>{c.title}</b><div className="muted small">{c.detail}</div></div>
              {onOpen && <button className="btn mini" onClick={() => onOpen(c.page)}>Открыть</button>}
            </li>
          ))}
        </ul>
      )}
      {open && <SetupWizard reopened onGo={(page) => onOpen?.(page)} onClose={() => { setOpen(false); void load(); }} />}
    </section>
  );
}
