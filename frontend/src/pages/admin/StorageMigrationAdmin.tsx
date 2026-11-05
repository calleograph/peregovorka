import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type StorageMigration, type StorageMigrationDetail, type StorageProfile } from "../../api";
import { bytes } from "../../util";
import { ConfirmDialog, Modal } from "../../components/Dialogs";
import { STATE_TITLE, duration, enoughSpace, isLive, isOpen, remaining } from "../../storageMigration";

const BADGE: Partial<Record<StorageMigration["state"], string>> = { cleaned: "ok", switched: "ok", attention: "warn", waiting: "warn", failed: "bad" };

function NewMigration({ profiles, groups, onDone, onClose }: { profiles: StorageProfile[]; groups: Record<string, string>; onDone: () => void; onClose: () => void }) {
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [mode, setMode] = useState<"full" | "selected">("full");
  const [chosen, setChosen] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const toggle = (k: string) => setChosen((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]));
  const ready = source && target && source !== target && (mode === "full" || chosen.length > 0);
  const go = async () => {
    setBusy(true); setErr("");
    try { await api.admin.createMigration({ source_id: source, target_id: target, groups: mode === "full" ? null : chosen }); onDone(); }
    catch (e) { setErr((e as ApiError).message); setBusy(false); }
  };
  return (
    <Modal title="Перенести данные между хранилищами" onClose={onClose}>
      <label className="field">Откуда<select value={source} onChange={(e) => setSource(e.target.value)}><option value="">Выберите хранилище</option>
        {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label className="field">Куда<select value={target} onChange={(e) => setTarget(e.target.value)}><option value="">Выберите хранилище</option>
        {profiles.filter((p) => p.id !== source).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <fieldset className="field"><legend>Что переносить</legend>
        <label className="check"><input type="radio" name="mig-mode" checked={mode === "full"} onChange={() => setMode("full")} /> Всё, что лежит в этом хранилище</label>
        <label className="check"><input type="radio" name="mig-mode" checked={mode === "selected"} onChange={() => setMode("selected")} /> Выбрать данные</label>
        {mode === "selected" && Object.entries(groups).map(([k, title]) => <label className="check" key={k}><input type="checkbox" checked={chosen.includes(k)} onChange={() => toggle(k)} /> {title}</label>)}
      </fieldset>
      <p className="muted small">Сначала будет показана оценка: объём, свободное место и примерное время. Копирование начнётся только после вашего подтверждения.</p>
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="row"><div className="spacer" /><button className="btn ghost" onClick={onClose}>Отмена</button><button className="btn primary" disabled={!ready || busy} onClick={() => void go()}>{busy ? "Создание…" : "Оценить"}</button></div>
    </Modal>
  );
}

function Estimate({ j }: { j: StorageMigration }) {
  const e = j.estimate;
  if (!e) return null;
  return (
    <>
      <div className="table-scroll"><table className="table"><thead><tr><th>Данные</th><th>Файлов</th><th>Объём</th></tr></thead>
        <tbody>{e.categories.map((c) => <tr key={c.key}><td>{c.title}</td><td>{c.files}</td><td>{bytes(c.bytes)}</td></tr>)}
          {!e.categories.length && <tr><td colSpan={3} className="muted">В этом хранилище нет данных выбранных видов.</td></tr>}</tbody></table></div>
      <div className="muted small">Всего {j.total} файлов, {bytes(j.bytes_total)} · свободно на «{j.target}»: {e.target_free == null ? "неизвестно" : bytes(e.target_free)} · примерное время: {duration(e.seconds)}</div>
      <div className="muted small">{e.not_in_profiles}</div>
    </>
  );
}

function Report({ j }: { j: StorageMigration }) {
  const r = j.report;
  if (!r) return null;
  return (
    <div className="small" role="status">
      <div>Перенесено файлов: {r.moved_files} ({bytes(r.moved_bytes)}). Освобождено на «{j.source}»: {bytes(r.freed_bytes)}.</div>
      {r.gone_files > 0 && <div>Исчезли с источника до копирования: {r.gone_files}.</div>}
      {r.kept.length > 0 && <div>Оставлено на источнике: {r.kept.map((k) => `${k.reason}: ${k.files}`).join("; ")}.</div>}
    </div>
  );
}

function Job({ j, onChange }: { j: StorageMigration; onChange: () => void }) {
  const [detail, setDetail] = useState<StorageMigrationDetail | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [err, setErr] = useState("");
  const act = async (f: () => Promise<unknown>) => { setErr(""); try { await f(); onChange(); } catch (e) { setErr((e as ApiError).message); } };
  const refresh = useCallback(() => api.admin.migration(j.id).then(setDetail).catch((e) => setErr((e as ApiError).message)), [j.id]);
  const left = remaining(j);
  return (
    <div className="card">
      <div className="row"><b>{j.source} → {j.target}</b><span className={`badge ${BADGE[j.state] ?? ""}`}>{STATE_TITLE[j.state]}</span>
        <div className="spacer" />
        {j.state === "ready" && <button className="btn mini primary" disabled={!enoughSpace(j)} onClick={() => void act(() => api.admin.startMigration(j.id))}>Начать перенос</button>}
        {j.state === "attention" && <button className="btn mini primary" onClick={() => void act(() => api.admin.retryMigration(j.id, false))}>Повторить ошибки</button>}
        {j.state === "attention" && <button className="btn mini" onClick={() => void act(() => api.admin.retryMigration(j.id, true))}>Повторить с заменой</button>}
        {j.state === "switched" && <button className="btn mini danger" onClick={() => setCleaning(true)}>Очистить источник</button>}
        {(["scanning", "ready", "copying", "attention", "verifying", "waiting"] as StorageMigration["state"][]).includes(j.state) && <button className="btn mini" onClick={() => void act(() => api.admin.cancelMigration(j.id))}>Отменить</button>}
        <button className="btn mini ghost" aria-expanded={!!detail} onClick={() => (detail ? setDetail(null) : void refresh())}>{detail ? "Скрыть журнал" : "Журнал"}</button>
      </div>
      {j.state === "ready" && <Estimate j={j} />}
      {j.state === "scanning" && <div className="muted small">Просмотрено файлов: {j.total}</div>}
      {j.state === "ready" && !enoughSpace(j) && <div className="alert error small" role="alert">На «{j.target}» не хватает места: освободите его или выберите меньше данных.</div>}
      {["copying", "verifying", "waiting", "attention"].includes(j.state) && <>
        <div className="bar" role="progressbar" aria-valuenow={j.percent} aria-valuemin={0} aria-valuemax={100} aria-label="Прогресс переноса"><i style={{ width: `${j.percent}%` }} /></div>
        <div className="muted small">{j.percent}% · файлов {j.done} из {j.total} · {bytes(j.bytes_done)} из {bytes(j.bytes_total)}{j.speed_bps ? ` · ${bytes(j.speed_bps)}/с` : ""}{left != null && j.state === "copying" ? ` · осталось ${duration(left)}` : ""}{j.failed ? ` · ошибок ${j.failed}` : ""}</div>
      </>}
      {j.state === "switched" && <div className="alert small" role="status">Система работает на «{j.target}», новые файлы пишутся туда. Файлы на «{j.source}» сохранены. Проверьте встречи и записи, затем очистите источник.</div>}
      {j.error && <div className={`alert ${j.state === "waiting" ? "warn" : "error"} small`} role="alert">{j.error}</div>}
      {err && <div className="alert error small" role="alert">{err}</div>}
      <Report j={j} />
      {detail && <div className="small">
        {detail.problems.length > 0 && <ul>{detail.problems.map((p) => <li key={p.path}><code>{p.path}</code>: {p.error}</li>)}</ul>}
        <ul className="muted">{[...detail.events].reverse().map((e) => <li key={e.t + e.text}>{new Date(e.t).toLocaleString("ru-RU")} — {e.text}</li>)}</ul>
      </div>}
      {cleaning && <ConfirmDialog title="Очистить исходное хранилище" typed={j.source} confirmLabel="Удалить перенесённые файлы"
        body={<>С «{j.source}» будут удалены только перенесённые и не изменившиеся файлы. Перед удалением каждого файла проверяется копия на «{j.target}». Отменить удаление нельзя.</>}
        onConfirm={async () => { await api.admin.cleanMigration(j.id, j.source); onChange(); }} onClose={() => setCleaning(false)} />}
    </div>
  );
}

/** «Перенести данные между хранилищами»: все файлы с одного подключённого хранилища на другое, с проверкой, переключением и отдельной очисткой. */
export default function StorageMigrationAdmin({ profiles }: { profiles: StorageProfile[] }) {
  const [jobs, setJobs] = useState<StorageMigration[]>([]);
  const [groups, setGroups] = useState<Record<string, string>>({});
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.migrations().then((r) => { setJobs(r.items); setGroups(r.groups); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const live = jobs.some(isLive);
  useEffect(() => { if (!live) return; const t = window.setInterval(() => void load(), 2000); return () => window.clearInterval(t); }, [live, load]);
  return (
    <section aria-label="Перенос между хранилищами">
      <h3>Перенос между хранилищами</h3>
      <p className="muted small">Все данные с одного подключённого хранилища на другое: записи, документы встреч, вложения, журнал и копии настроек. Файлы проверяются по контрольной сумме, система переключается на новое хранилище, а старое очищается только по вашей команде.</p>
      <div className="row"><button className="btn primary" disabled={profiles.length < 2 || jobs.some(isOpen)} onClick={() => setOpen(true)}>Перенести данные между хранилищами</button></div>
      {profiles.length < 2 && <p className="muted small">Сначала подключите второе хранилище.</p>}
      {err && <div className="alert error" role="alert">{err}</div>}
      {jobs.slice(0, 5).map((j) => <Job key={j.id} j={j} onChange={() => void load()} />)}
      {open && <NewMigration profiles={profiles} groups={groups} onDone={() => { setOpen(false); void load(); }} onClose={() => setOpen(false)} />}
    </section>
  );
}
