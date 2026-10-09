import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type SyncOverview, type SyncRun } from "../../api";
import SettingsForm from "./SettingsForm";
import { syncFields } from "./fields";
import { fmtDate } from "./common";

const STATUS: Record<string, [string, string]> = {
  running: ["идёт…", "badge"], ok: ["без замечаний", "badge ok"], partial: ["есть замечания", "badge warn"], unavailable: ["хранилище недоступно", "badge warn"], failed: ["прервана", "badge warn"],
};

function Report({ run }: { run: SyncRun }) {
  const d = run.details;
  return (
    <div className="card">
      <div className="row"><h3 style={{ margin: 0 }}>Отчёт о сверке</h3><span className={STATUS[run.status]?.[1] ?? "badge"}>{STATUS[run.status]?.[0] ?? run.status}</span>
        <span className="muted small">{fmtDate(run.started_at)} · {run.trigger === "manual" ? `вручную${run.actor ? `: ${run.actor}` : ""}` : "по расписанию"}</span></div>
      <div className="stats-row">
        <div><b>{run.checked}</b><span>проверено</span></div>
        <div className={run.missing ? "warn-n" : ""}><b>{run.missing}</b><span>отсутствует</span></div>
        <div><b>{run.restored}</b><span>снова на месте</span></div>
        <div><b>{run.orphans}</b><span>неизвестных файлов</span></div>
        <div className={run.unavailable ? "warn-n" : ""}><b>{run.unavailable}</b><span>не удалось проверить</span></div>
      </div>
      {d?.suspicious && Object.keys(d.suspicious).length > 0 && <div className="alert error" role="alert"><b>Изменения не применены.</b> {Object.values(d.suspicious).join(" ")} Проверьте, что хранилище подключено и открыто то, что нужно; если файлы действительно удалены — запустите сверку с подтверждением.</div>}
      {d?.unavailable_backends && Object.keys(d.unavailable_backends).length > 0 && (
        <div className="alert info">Недоступны (файлы НЕ считаются удалёнными, повторите позже): {Object.entries(d.unavailable_backends).map(([k, v]) => `${k}: ${v}`).join("; ")}</div>)}
      {d?.per_kind && Object.keys(d.per_kind).length > 0 && (
        <table className="table"><thead><tr><th>Что</th><th>Проверено</th><th>Отсутствует</th><th>Вернулось</th><th>Не проверено</th></tr></thead>
          <tbody>{Object.entries(d.per_kind).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v.checked}</td><td>{v.missing}</td><td>{v.restored}</td><td>{v.unavailable}</td></tr>)}</tbody></table>)}
      {!!d?.missing?.length && (<><h4>Не найдены в хранилище</h4><ul className="small plain">{d.missing.map((m, i) => <li key={i}>{m.kind}: <code>{m.name}</code> <span className="muted">({m.dir})</span></li>)}</ul>
        <p className="muted small">Для таких записей ссылка на скачивание больше не выдаётся; файл можно вернуть на место — при следующей сверке он снова станет доступным.</p></>)}
      {!!d?.orphans?.length && (<><h4>Неизвестные файлы</h4><ul className="small plain">{d.orphans.map((m, i) => <li key={i}><code>{m.path}</code> <span className="muted">({m.storage})</span></li>)}</ul>
        <p className="muted small">Это файлы, о которых система ничего не знает. Они не импортируются и не удаляются — решите сами, нужны ли они.</p></>)}
      {d?.orphan_scan_skipped && <p className="muted small">Объектов очень много — поиск неизвестных файлов в этот раз пропущен (проверка наличия выполнена).</p>}
    </div>
  );
}

/** Сверка базы с реальным содержимым хранилищ: ручной запуск, расписание, отчёт. Выполняется в фоне пакетами — не при открытии страниц. */
export default function StorageSyncAdmin() {
  const [ov, setOv] = useState<SyncOverview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.admin.syncOverview().then(setOv).catch((e) => setError((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!ov?.running) return;
    const t = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(t);
  }, [ov?.running, load]);

  const run = async (force = false) => {
    if (force && !window.confirm("Применить результат сверки, даже если пропали почти все файлы? Делайте это, только если файлы действительно были удалены, а не отключено хранилище.")) return;
    setBusy(true); setError("");
    try { await api.admin.syncRun(force); await load(); } catch (e) { setError((e as ApiError).message); }
    setBusy(false);
  };
  const last = ov?.last;
  const suspicious = !!last?.details?.suspicious && Object.keys(last.details.suspicious).length > 0;

  return (
    <section>
      <div className="row"><h2>Проверка целостности хранилищ</h2><div className="spacer" />
        <button className="btn primary" onClick={() => void run(false)} disabled={busy || !!ov?.running}>{ov?.running ? "Сверка идёт…" : "Проверить / синхронизировать хранилище"}</button></div>
      <p className="muted">База данных не считает файл доступным только потому, что он когда-то был создан. Сверка сравнивает записи (аудио, стенограммы, протоколы, вложения чата, переписку и схемы досок) с тем, что реально лежит в хранилище.
        Если файл удалили вручную (например, прямо на SMB-ресурсе), он перестаёт предлагаться пользователям, а состояние фиксируется в базе и в аудите. Неизвестные файлы <b>не</b> импортируются — они только показываются в отчёте.
        При недоступном хранилище файлы не объявляются удалёнными. Сверка идёт в фоне пакетами и не замедляет страницы.</p>
      {error && <div className="alert error" role="alert">{error}</div>}
      {ov?.running && <div className="alert info" role="status">Сверка запущена {fmtDate(ov.running.started_at)} — отчёт появится здесь, когда она закончится.</div>}
      {suspicious && <div className="row"><button className="btn danger" onClick={() => void run(true)} disabled={busy || !!ov?.running}>Применить несмотря на предупреждение…</button></div>}
      {last && <Report run={last} />}
      {!last && !ov?.running && ov && <div className="alert info">Сверок ещё не было. Нажмите кнопку выше или дождитесь запуска по расписанию.</div>}

      <SettingsForm group="storage_sync" title="Расписание" fields={syncFields} note="Автоматическая сверка запускается не чаще, чем раз в указанное число часов." />
      {ov && ov.runs.length > 1 && (
        <>
          <h3>Прошлые запуски</h3>
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Начало</th><th>Как</th><th>Итог</th><th>Проверено</th><th>Отсутствует</th><th>Вернулось</th><th>Неизвестных</th><th>Не проверено</th></tr></thead>
            <tbody>{ov.runs.map((r) => (
              <tr key={r.id}><td className="small">{fmtDate(r.started_at)}</td><td className="small">{r.trigger === "manual" ? `вручную${r.actor ? `: ${r.actor}` : ""}` : "по расписанию"}</td>
                <td><span className={STATUS[r.status]?.[1] ?? "badge"}>{STATUS[r.status]?.[0] ?? r.status}</span></td><td>{r.checked}</td><td>{r.missing}</td><td>{r.restored}</td><td>{r.orphans}</td><td>{r.unavailable}</td></tr>))}</tbody>
          </table></div>
        </>
      )}
    </section>
  );
}
