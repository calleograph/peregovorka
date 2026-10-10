import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type StorageStats, type StorageVolume } from "../../api";
import { bytes } from "../../util";
import { fmtDate } from "./common";
import { volumeLabel } from "../../storageMath";

const KIND_TITLE: Record<string, string> = { mix_video: "Общее видео встреч", mix_audio: "Общее аудио встреч", participant: "Индивидуальное аудио участников" };

function Bar({ value }: { value: number }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return <div className={`bar ${v >= 90 ? "bad" : v >= 75 ? "warn" : ""}`}><i style={{ width: `${v}%` }} /></div>;
}

function Volume({ v }: { v: StorageVolume }) {
  const st = volumeLabel(v);
  return (
    <div className="card storage-volume">
      <div className="row"><b>{v.title}</b><span className={`badge ${st.tone}`}>{st.text}</span><div className="spacer" /><span className="muted small">{v.kind === "smb" ? "SMB" : v.kind === "local" ? "каталог" : ""}</span></div>
      {v.address && <div className="muted small" style={{ wordBreak: "break-all" }}>{v.address}</div>}
      {v.state === "not_configured" && <p className="muted small">Не настроено: включите внешнее хранилище в «Файловые хранилища» и «Аудиозаписи».</p>}
      {v.state === "unavailable" && <div className="alert warn small" role="alert">Хранилище недоступно: {v.error ?? "нет ответа"}.{v.stale && v.last_ok_at ? ` Показаны данные последнего удачного замера (${fmtDate(v.last_ok_at)}) — они могли устареть.` : ""}</div>}
      {v.total != null && v.free != null && (
        <>
          <div className="row" style={{ marginTop: 6 }}><span>Занято {v.used_percent ?? "—"} %</span><span className="muted small">свободно {bytes(v.free)} из {bytes(v.total)}{v.stale ? " · устарело" : ""}</span></div>
          <Bar value={v.used_percent ?? 0} />
        </>
      )}
      {v.state !== "not_configured" && (
        <table className="table compact" style={{ marginTop: 8 }}><thead><tr><th>Что</th><th>Файлов</th><th>Объём</th></tr></thead><tbody>
          {Object.keys(KIND_TITLE).map((k) => <tr key={k}><td>{KIND_TITLE[k]}</td><td>{v.files[k]?.count ?? 0}</td><td>{bytes(v.files[k]?.bytes ?? 0)}</td></tr>)}
        </tbody></table>
      )}
      {v.state !== "not_configured" && (
        <div className="small" style={{ marginTop: 6 }}>
          <div>По данным базы: <b>{bytes(v.db_bytes)}</b>{v.disk_bytes != null ? <> · фактически файлов на диске: <b>{bytes(v.disk_bytes)}</b>{v.scan_truncated ? " (подсчёт неполный)" : ""}</> : <span className="muted"> · фактический подсчёт для SMB делает «Сверка хранилища»</span>}</div>
          {v.total != null && v.free != null && <div className="muted">Занято на томе всего: {bytes(v.total - v.free)} (включая данные, не относящиеся к записям).</div>}
          {v.mismatch && <div className="alert warn small" role="status">Размеры расходятся: на диске лежат файлы, которых нет в базе (осиротевшие или недокопированные), либо часть файлов пропала. Запустите «Сверку хранилища».</div>}
        </div>
      )}
      <div className="muted small" style={{ marginTop: 4 }}>Замер: {fmtDate(v.measured_at)}</div>
    </div>
  );
}

/** «Хранилище записей» в «Технических показателях»: готовый фоновый снимок (страница не обращается к SMB и не обходит каталоги). */
export default function StorageStatsCard() {
  const [data, setData] = useState<StorageStats | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.storageStats().then((d) => { setData(d); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (!data?.refreshing) return; const t = window.setInterval(() => void load(), 3000); return () => window.clearInterval(t); }, [data?.refreshing, load]);
  const refresh = async () => { try { await api.admin.storageStatsRefresh(); } catch (e) { setErr((e as ApiError).message); } await load(); setTimeout(() => void load(), 1200); };

  return (
    <section aria-label="Хранилище записей">
      <div className="row"><h3 style={{ margin: 0 }}>Хранилище записей</h3><div className="spacer" />
        <button className="btn mini" onClick={() => void refresh()} disabled={!!data?.refreshing}>{data?.refreshing ? "Измеряется…" : "Обновить"}</button></div>
      <p className="muted small">Данные считаются в фоне раз в 10 минут и по кнопке; страница показывает готовый замер{data?.measured_at ? ` от ${fmtDate(data.measured_at)}` : ""}.</p>
      {err && <div className="alert error" role="alert">{err}</div>}
      {data && !data.volumes.length && <p className="muted">{data.note ?? "Нет данных."}</p>}
      <div className="grid-2">{data?.volumes.map((v) => <Volume key={v.id} v={v} />)}</div>
      {data?.sync && <p className="muted small">Последняя сверка{data.sync.at ? ` (${fmtDate(data.sync.at)})` : ""}: {data.sync.status === "ok" ? "расхождений нет" : data.sync.status}; неизвестных файлов в хранилище: {data.sync.orphans}, пропавших: {data.sync.missing}.</p>}
      {data?.other && <p className="muted small">{data.other.title}: {data.other.count} файлов, {bytes(data.other.bytes)}. {data.other.note}</p>}
    </section>
  );
}
