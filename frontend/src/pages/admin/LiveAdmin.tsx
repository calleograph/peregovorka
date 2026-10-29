import { useCallback, useEffect, useState } from "react";
import { api, type LiveAdminStatus } from "../../api";
import { bytes } from "../../util";
import SettingsForm, { type Field } from "./SettingsForm";

/** «Трансляции»: идущие эфиры, зрители, место на томе HLS, предупреждения и настройки. Службу Egress (общую с видеозаписью) ставит администратор: sudo scripts/video.sh enable. */
export default function LiveAdmin({ fields }: { fields: Field[] }) {
  const [st, setSt] = useState<LiveAdminStatus | null>(null);
  const [err, setErr] = useState("");
  const [cdnMsg, setCdnMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [cdnBusy, setCdnBusy] = useState(false);
  const checkCdn = async () => { setCdnBusy(true); setCdnMsg(null); try { const r = await api.adminLiveCdnCheck(); setCdnMsg({ ok: r.ok, text: r.message }); } catch (e) { setCdnMsg({ ok: false, text: (e as Error).message }); } finally { setCdnBusy(false); } };
  const load = useCallback(() => { api.adminLiveStatus().then((s) => { setSt(s); setErr(""); }).catch((e) => setErr((e as Error).message || "Не удалось получить состояние")); }, []);
  useEffect(() => { load(); const t = window.setInterval(load, 10000); return () => window.clearInterval(t); }, [load]);
  return (
    <>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row"><b>Состояние</b><span className={`badge ${st?.enabled ? "ok" : ""}`}>{st ? (st.enabled ? "включены" : "выключены") : "…"}</span>
          <div className="spacer" /><button className="btn mini" onClick={load}>Обновить</button></div>
        {err && <div className="alert warn small" role="alert">{err}</div>}
        {st && (
          <>
            <p className="small" style={{ margin: "8px 0 0" }}>
              Идёт эфиров: <b>{st.running}</b> из {st.max_concurrent} · зрителей сейчас: <b>{st.viewers}</b> · нагрузка Egress: <b>{st.egress_cpu_load == null ? "нет данных" : `${st.egress_cpu_load}%`}</b> (предел {st.max_cpu_load}%) · площадок: идёт <b>{st.destinations.live ?? 0}</b>, с ошибкой <b>{st.destinations.failed ?? 0}</b> · записей MP4 идёт: <b>{st.recordings_running}</b> · сегментов HLS на диске: <b>{bytes(st.hls_bytes)}</b> · свободно на томе: <b>{st.free_bytes == null ? "нет данных" : bytes(st.free_bytes)}</b>
            </p>
            {st.warnings.map((w) => <div key={w} className="alert warn small" role="alert">{w}</div>)}
            {st.active.length > 0 && (
              <table className="table small" style={{ marginTop: 8 }}>
                <thead><tr><th>Комната</th><th>Состояние</th><th>Качество</th><th>Зрителей</th><th>С</th><th>Запустил</th></tr></thead>
                <tbody>{st.active.map((a, i) => <tr key={i}><td>{a.room}</td><td>{a.status === "live" ? "идёт" : a.status === "starting" ? "запускается" : "заканчивается"}{a.hls && !a.hls_ready ? " (плейлист готовится)" : ""}</td><td>{a.quality}</td><td>{a.viewers}</td><td>{new Date(a.since).toLocaleTimeString("ru-RU")}</td><td>{a.by ?? "—"}</td></tr>)}</tbody>
              </table>)}
            {st.failed.length > 0 && <details style={{ marginTop: 8 }}><summary className="small">Последние прерванные эфиры ({st.failed.length})</summary>
              <ul className="small">{st.failed.map((f, i) => <li key={i}>{f.room}: {f.error ?? "причина не указана"} <span className="muted">({new Date(f.at).toLocaleString("ru-RU")})</span></li>)}</ul></details>}
          </>
        )}
        <p className="muted small" style={{ marginBottom: 0 }}>Эфир ведёт тот же контейнер Egress, что и видеозапись (<code>sudo scripts/video.sh enable</code>); нагрузку процессора и сети показывает <code>docker stats</code>. Новый эфир не стартует при нехватке места или превышении предела, а идущие конференции от этого не страдают.</p>
      </div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row"><b>CDN</b><span className={`badge ${st?.cdn.enabled ? "ok" : ""}`}>{st ? (st.cdn.enabled ? "подключён" : "не подключён") : "…"}</span><div className="spacer" />
          <button className="btn mini" onClick={() => void checkCdn()} disabled={cdnBusy || !st?.cdn.base_url}>{cdnBusy ? "Проверка…" : "Проверить CDN"}</button></div>
        {cdnMsg && <div className={`alert ${cdnMsg.ok ? "ok" : "warn"} small`} role="status">{cdnMsg.text}</div>}
        <p className="muted small" style={{ marginBottom: 0 }}>CDN должен забирать файлы с этого сервера по тем же путям (<code>/hls/…</code>) и отдавать их зрителям, кэшируя сегменты (<code>.ts</code>) и почти не кэшируя плейлист (1 секунда): так задают заголовки источника. Укажите «Адрес доставки CDN», включите «Раздавать HLS через CDN», сохраните, затем «Проверить CDN»; в комнате включите «Раздавать через CDN». Подпись в пути одинакова у всех зрителей в окне 5 минут: CDN не плодит копии.</p>
      </div>
      <SettingsForm key="live" group="live" title="Трансляции" fields={fields}
        intro="Зрители смотрят HLS, который nginx раздаёт напрямую с этого сервера (подходит для небольших аудиторий), без подключения к LiveKit. Включается по комнатам: тип «Трансляционная» или «Настройки комнаты → Трансляция»." />
    </>
  );
}
