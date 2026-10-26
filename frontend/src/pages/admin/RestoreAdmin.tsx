import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";
import { countsText, defaultSelection, FILE_STATE, filesText, isActive, jobStatus, jobTitle, KIND_LABEL, selectable, STATUS, summary, TRUST, type RestoreItem, type RestoreJobView } from "../../restoreView";
import { fmtDate } from "./common";

const errText = (e: unknown) => (e as ApiError)?.message || "Не удалось выполнить действие";

function Details({ it }: { it: RestoreItem }) {
  return (
    <div className="stack small" style={{ padding: "6px 4px 10px" }}>
      <div><b>{it.how}</b></div>
      <div className="muted">{it.trust_note}</div>
      {it.note && <div>{it.note}</div>}
      {it.problems.length > 0 && <ul className="plain">{it.problems.map((p, i) => <li key={i} className="bad-text">{p}</li>)}</ul>}
      <table className="table compact"><thead><tr><th>Папка</th><th>Файл</th><th>Что это</th><th>Проверка</th></tr></thead>
        <tbody>{it.files.map((f, i) => <tr key={i}><td>{f.area}</td><td><code>{f.name}</code></td><td>{f.kind ? KIND_LABEL[f.kind] ?? f.kind : "—"}</td><td className={f.state === "mismatch" || f.state === "missing" || f.state === "unreadable" ? "bad-text" : ""}>{FILE_STATE[f.state] ?? f.state}</td></tr>)}</tbody></table>
      {it.mode === "manifest" && it.trust === "valid" && <div className="muted">Участники по паспорту: найдено в базе {it.people.found}, не найдено {it.people.missing} (им доступ выдан не будет).</div>}
    </div>
  );
}

/** Администрирование → Хранилища → «Восстановление материалов»: найти материалы встреч в хранилищах и вернуть их в приложение после переноса. */
export default function RestoreAdmin() {
  const [jobs, setJobs] = useState<RestoreJobView[]>([]);
  const [scan, setScan] = useState<RestoreJobView | null>(null);
  const [run, setRun] = useState<RestoreJobView | null>(null);
  const [sub, setSub] = useState("");
  const [deep, setDeep] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [onlyNew, setOnlyNew] = useState(true);
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const loadList = useCallback(() => api.admin.restoreOverview().then((o) => { setJobs(o.jobs); return o.jobs; }).catch((e) => { setErr(errText(e)); return [] as RestoreJobView[]; }), []);
  const follow = useCallback(async (id: string, kind: "scan" | "restore") => {
    window.clearTimeout(timer.current);
    const tick = async () => {
      try {
        const j = await api.admin.restoreJob(id);
        (kind === "scan" ? setScan : setRun)(j);
        if (j.status === "running") timer.current = window.setTimeout(() => void tick(), 1500);
        else { void loadList(); if (kind === "scan" && j.status === "done") setChosen(defaultSelection(j.result?.items ?? [])); }
      } catch (e) { setErr(errText(e)); }
    };
    await tick();
  }, [loadList]);

  useEffect(() => {
    void loadList().then((list) => {
      const active = list.find((j) => j.status === "running");
      if (active) void follow(active.id, active.kind);
      else { const last = list.find((j) => j.kind === "scan" && j.status === "done"); if (last) void follow(last.id, "scan"); }
    });
    return () => window.clearTimeout(timer.current);
  }, [loadList, follow]);

  const start = async () => {
    setBusy(true); setErr(""); setRun(null);
    try { const j = await api.admin.restoreScan(sub.trim(), deep); setScan(j); void follow(j.id, "scan"); } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  const cancel = async (j: RestoreJobView) => { try { await api.admin.restoreCancel(j.id); } catch (e) { setErr(errText(e)); } };
  const apply = async () => {
    if (!scan) return;
    const j = await api.admin.restoreApply(scan.id, [...chosen]);
    setRun(j);
    void follow(j.id, "restore");
  };

  const items = scan?.result?.items ?? [];
  const shown = useMemo(() => items.filter((i) => !onlyNew || i.status !== "present"), [items, onlyNew]);
  const sum = summary(items, chosen);
  const scanning = isActive(scan);
  const restoring = isActive(run);
  const toggle = (id: string) => setChosen((c) => { const n = new Set(c); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <section>
      <h2>Восстановление материалов</h2>
      <p className="muted">Если материалы встреч остались в хранилище (записи, стенограммы, протоколы, схемы доски), а приложение их не показывает — например, после переноса на другой сервер или диск, — здесь их можно найти и вернуть.
        Основной способ переноса остаётся прежним: база и хранилище переносятся вместе. Эта страница — дополнение; она ищет файлы в хранилищах, настроенных в разделах «Хранилище документов встреч» и «Хранилище аудиозаписей».</p>
      <div className="alert info"><b>Как это работает.</b> У встреч, записанных начиная с выпуска 0.40, рядом с материалами лежит подписанный паспорт (<code>manifest.json</code>): по нему <b>достоверно</b> восстанавливаются встреча, комната, участники и права —
        только для существующих учётных записей и только если подпись проверена ключом этой установки (APP_MASTER_KEY из резервной копии). Всё остальное (старые каталоги, чужая или изменённая подпись) восстанавливается <b>предположительно</b>:
        в изолированную комнату «Восстановленные материалы», видимую только администратору, без участников и прав. Исходные файлы не изменяются и не удаляются.</div>

      <div className="card stack">
        <h3 style={{ margin: 0 }}>Найти материалы</h3>
        <label>Подкаталог (необязательно)<input value={sub} onChange={(e) => setSub(e.target.value)} placeholder="Например: Переговорная Север/2026-10-24, Суббота" disabled={scanning || restoring} maxLength={300} />
          <span className="help">Пусто — искать во всех хранилищах целиком. Путь указывается внутри папок Audio, Transcripts, Protocols, Chat, Boards.</span></label>
        <label className="check"><input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} disabled={scanning || restoring} />
          <span className="check-body">Проверить контрольные суммы при поиске<span className="help">Читает каждый файл целиком — для больших архивов долго. Без этого при поиске сверяются имена и размеры, а суммы проверяются при восстановлении по каждому файлу.</span></span></label>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row">
          <button className="btn primary" onClick={() => void start()} disabled={busy || scanning || restoring}>{scanning ? "Идёт поиск…" : "Найти и восстановить материалы встреч"}</button>
          {scanning && <button className="btn" onClick={() => scan && void cancel(scan)}>Отменить</button>}
          {scanning && <span className="muted small">Просмотрено каталогов: {String((scan?.progress as { dirs?: number })?.dirs ?? 0)}. Поиск идёт в фоне и мало нагружает диск; страницу можно закрыть.</span>}
        </div>
      </div>

      {scan && scan.status !== "running" && (
        <div className="card stack" style={{ marginTop: 12 }}>
          <div className="row"><h3 style={{ margin: 0 }}>Результат поиска</h3><span className="muted small">{fmtDate(scan.created_at)}</span><div className="spacer" />
            <label className="check"><input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} /> скрыть уже имеющиеся</label></div>
          {scan.status !== "done" ? <div className="alert warn">{scan.status === "cancelled" ? "Поиск отменён." : jobStatus(scan.status)} {scan.error ?? ""}</div> : (
            <>
              <div className="stats-row">
                <div><b>{scan.result?.counts?.found ?? 0}</b><span>папок найдено</span></div>
                <div><b>{scan.result?.counts?.verified ?? 0}</b><span>достоверно (подпись верна)</span></div>
                <div><b>{scan.result?.counts?.supposed ?? 0}</b><span>предположительно</span></div>
                <div><b>{scan.result?.counts?.present ?? 0}</b><span>уже в приложении</span></div>
              </div>
              {scan.result?.truncated && <div className="alert warn">Папок слишком много: показана часть. Уточните подкаталог и повторите поиск.</div>}
              {shown.length === 0 ? <p className="muted">Новых материалов не найдено.</p> : (
                <div style={{ overflowX: "auto" }}>
                  <table className="table compact">
                    <thead><tr><th><span className="sr-only">Выбрать</span></th><th>Встреча (каталог)</th><th>Достоверность</th><th>Состояние</th><th>Материалы</th></tr></thead>
                    <tbody>
                      {shown.map((it) => (
                        <Fragment key={it.id}>
                          <tr>
                            <td><input type="checkbox" aria-label={`Выбрать: ${it.dir}`} checked={chosen.has(it.id)} disabled={!selectable(it) || restoring} onChange={() => toggle(it.id)} /></td>
                            <td><button className="linkish" onClick={() => setOpen(open === it.id ? null : it.id)} aria-expanded={open === it.id}>{it.room?.name || it.dir.split("/")[0]}</button><div className="muted small">{it.dir}{it.started_at ? ` · ${fmtDate(it.started_at)}` : ""}</div></td>
                            <td><span className={TRUST[it.trust].cls}>{TRUST[it.trust].label}</span></td>
                            <td><span className={STATUS[it.status].cls}>{STATUS[it.status].label}</span>{it.problems.length > 0 && <div className="bad-text small">проблем: {it.problems.length}</div>}</td>
                            <td className="small">{filesText(it.files)}</td>
                          </tr>
                          {open === it.id && <tr><td /><td colSpan={4}><Details it={it} /></td></tr>}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="row">
                <button className="btn" onClick={() => setChosen(defaultSelection(items))} disabled={restoring}>Отметить достоверные</button>
                <button className="btn" onClick={() => setChosen(new Set(items.filter(selectable).map((i) => i.id)))} disabled={restoring}>Отметить все</button>
                <button className="btn" onClick={() => setChosen(new Set())} disabled={restoring}>Снять отметки</button>
                <div className="spacer" />
                <span className="muted small">Выбрано: достоверно {sum.certain}, предположительно {sum.supposed}</span>
                <button className="btn primary" disabled={chosen.size === 0 || restoring} onClick={() => setConfirm(true)}>Восстановить выбранное</button>
              </div>
            </>
          )}
        </div>
      )}

      {run && (
        <div className="card stack" style={{ marginTop: 12 }}>
          <div className="row"><h3 style={{ margin: 0 }}>Восстановление</h3><span className={`badge ${run.status === "done" ? "ok" : run.status === "running" ? "" : "warn"}`}>{jobStatus(run.status)}</span>
            {restoring && <button className="btn mini" onClick={() => void cancel(run)}>Отменить</button>}</div>
          {restoring && <p className="muted small">Обработано: {String((run.progress as { done?: number })?.done ?? 0)} из {String((run.progress as { total?: number })?.total ?? chosen.size)}. {String((run.progress as { current?: string })?.current ?? "")}</p>}
          {run.error && <div className="alert error">{run.error}</div>}
          {run.result?.counts && <div className="stats-row"><div><b>{run.result.counts.restored}</b><span>достоверно</span></div><div><b>{run.result.counts.isolated}</b><span>в изолированную область</span></div><div><b>{run.result.counts.skipped}</b><span>пропущено</span></div><div className={run.result.counts.failed ? "warn-n" : ""}><b>{run.result.counts.failed}</b><span>с ошибкой</span></div></div>}
          {run.result?.report?.map((r) => (
            <details key={r.id}><summary><b>{r.dir}</b> — {r.outcome === "restored" ? "восстановлено достоверно" : r.outcome === "isolated" ? "помещено в изолированную область" : r.outcome === "failed" ? "ошибка" : "пропущено"}</summary>
              <ul className="small plain">{r.lines.map((l, i) => <li key={i}>{l}</li>)}</ul></details>))}
          {run.status === "done" && <p className="muted small">Изолированные материалы лежат в комнате «Восстановленные материалы» (выключена, видна администратору в разделе «Встречи»): перенесите нужные в рабочие комнаты вручную. Повторный запуск безопасен: уже восстановленное не дублируется.</p>}
        </div>
      )}

      <h3 style={{ marginTop: 18 }}>История заданий</h3>
      {jobs.length === 0 ? <p className="muted">Заданий ещё не было.</p> : (
        <table className="table compact"><thead><tr><th>Когда</th><th>Что</th><th>Кто запустил</th><th>Итог</th></tr></thead>
          <tbody>{jobs.map((j) => <tr key={j.id}><td>{fmtDate(j.created_at)}</td><td>{jobTitle(j)}</td><td>{j.started_by}</td>
            <td>{jobStatus(j.status)}{j.counts ? ` · ${countsText(j.counts)}` : ""}{j.error ? ` · ${j.error}` : ""}</td></tr>)}</tbody></table>)}
      {confirm && (
        <ConfirmDialog title="Восстановить выбранные материалы?" confirmLabel="Восстановить" danger={false} onClose={() => setConfirm(false)} onConfirm={apply}
          body={<div className="stack"><p>В приложение будут добавлены встречи и материалы: достоверно — {sum.certain}, предположительно (в изолированную область) — {sum.supposed}.</p>
            <p className="muted small">Исходные файлы в хранилище не изменяются и не удаляются. У достоверных встреч комната создаётся выключенной, если её нет; права берутся только из подписанного паспорта и только для существующих учётных записей. Операцию можно повторить: дубли не создаются.</p></div>} />
      )}
    </section>
  );
}
