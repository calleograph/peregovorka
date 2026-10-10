import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type TransferDetail, type TransferJob } from "../../api";
import { bytes } from "../../util";
import { fmtDate } from "./common";
import { DIRECTION_TITLE, nextStep, progressPercent, stateTitle } from "../../storageMath";

function Job({ j, onChange }: { j: TransferJob; onChange: () => void }) {
  const [detail, setDetail] = useState<TransferDetail | null>(null);
  const [err, setErr] = useState("");
  const act = async (f: () => Promise<unknown>) => { setErr(""); try { await f(); onChange(); } catch (e) { setErr((e as ApiError).message); } };
  const hint = nextStep(j);
  const live = j.state === "queued" || j.state === "running";
  return (
    <div className="card">
      <div className="row"><b>{DIRECTION_TITLE[j.direction]}</b><span className={`badge ${j.state === "done" && !j.failed ? "ok" : j.state === "failed" || j.failed ? "bad" : ""}`}>{stateTitle(j.state)}</span>
        <div className="spacer" />
        {live && <button className="btn mini" onClick={() => void act(() => api.admin.cancelTransfer(j.id))}>Остановить</button>}
        {j.state === "failed" && <button className="btn mini primary" onClick={() => void act(() => api.admin.resumeTransfer(j.id))}>Продолжить</button>}
        {(j.failed > 0 || j.skipped > 0) && <button className="btn mini" onClick={() => void api.admin.transfer(j.id).then(setDetail)}>Подробнее</button>}
      </div>
      <div className="bar"><i style={{ width: `${progressPercent(j)}%` }} /></div>
      <div className="muted small">Перенесено {j.done} из {j.total} ({bytes(j.bytes_done)} из {bytes(j.bytes_total)}) · пропущено {j.skipped} · ошибок {j.failed} · {j.scope === "meeting" ? "одна встреча" : "все записи"} · {j.created_by}, {fmtDate(j.created_at)}</div>
      {hint && <div className={`alert ${j.state === "failed" ? "error" : "warn"} small`} role="status">{hint}</div>}
      {err && <div className="alert error small" role="alert">{err}</div>}
      {detail && <ul className="small">{detail.problems.map((p) => <li key={p.recording_id}><code>{p.recording_id.slice(0, 8)}</code> — {p.state === "skipped" ? "пропущен" : "ошибка"}: {p.error}</li>)}</ul>}
    </div>
  );
}

/** «Перенос данных»: запись с локального диска на внешнее хранилище (SMB) и обратно. Работает в фоне; ссылки и права в истории не меняются. */
export default function StorageTransferAdmin() {
  const [jobs, setJobs] = useState<TransferJob[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.admin.transfers().then(setJobs).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const live = jobs.some((j) => j.state === "queued" || j.state === "running");
  useEffect(() => { if (!live) return; const t = window.setInterval(() => void load(), 3000); return () => window.clearInterval(t); }, [live, load]);
  const start = async (direction: TransferJob["direction"]) => {
    const q = direction === "to_external"
      ? "Перенести все записи с локального диска во внешнее хранилище? Файлы будут проверены (размер и контрольная сумма) и только затем удалены с диска."
      : "Вернуть все записи из внешнего хранилища на локальный диск? Файлы будут проверены и только затем удалены из хранилища.";
    if (!window.confirm(q)) return;
    setBusy(true); setErr("");
    try { await api.admin.startTransfer({ direction }); await load(); } catch (e) { setErr((e as ApiError).message); }
    setBusy(false);
  };
  return (
    <section aria-label="Перенос данных">
      <h3>Перенос данных</h3>
      <p className="muted small">Записи аудио можно перенести с локального диска во внешнее хранилище (SMB или сетевую папку из «Файловые хранилища → Аудиозаписи») и обратно — все сразу или по одной встрече (на странице встречи). Перенос идёт в фоне:
        для каждого файла проверяется источник и место, файл копируется, сверяются размер и контрольная сумма, затем запись в базе переключается на новое место, и только потом удаляется старый файл. Ссылки и права доступа не меняются.
        Файлы идущих встреч и записи, которые ещё формируются, пропускаются. Если хранилище недоступно или нет места, перенос останавливается — после исправления его можно продолжить.</p>
      <div className="row"><button className="btn primary" disabled={busy || live} onClick={() => void start("to_external")}>Перенести на внешнее хранилище</button>
        <button className="btn" disabled={busy || live} onClick={() => void start("to_local")}>Вернуть на локальный диск</button></div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {jobs.map((j) => <Job key={j.id} j={j} onChange={() => void load()} />)}
      {!jobs.length && <p className="muted small">Переносов ещё не было.</p>}
    </section>
  );
}
