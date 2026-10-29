import { useCallback, useEffect, useState } from "react";
import { api, type LiveAdminStatus } from "../../api";
import { bytes } from "../../util";
import InfoTip from "../../components/InfoTip";
import SettingsForm, { type Field } from "./SettingsForm";
import { ChecksList, ComponentCard, useComponents, WIKI } from "./ServerComponents";

/** «Трансляции»: служба Egress (общая с видеозаписью, включается из панели), проверки по цепочке, идущие эфиры и зрители, CDN и настройки. */
export default function LiveAdmin({ fields }: { fields: Field[] }) {
  const [st, setSt] = useState<LiveAdminStatus | null>(null);
  const [err, setErr] = useState("");
  const [cdnMsg, setCdnMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [cdnBusy, setCdnBusy] = useState(false);
  const ctl = useComponents();
  const checkCdn = async () => { setCdnBusy(true); setCdnMsg(null); try { const r = await api.adminLiveCdnCheck(); setCdnMsg({ ok: r.ok, text: r.message }); } catch (e) { setCdnMsg({ ok: false, text: (e as Error).message }); } finally { setCdnBusy(false); } };
  const load = useCallback(() => { api.adminLiveStatus().then((s) => { setSt(s); setErr(""); }).catch((e) => setErr((e as Error).message || "Не удалось получить состояние")); }, []);
  useEffect(() => { load(); const t = window.setInterval(load, 10000); return () => window.clearInterval(t); }, [load]);
  return (
    <>
      <div className="card" style={{ marginBottom: 12 }}>
        <ComponentCard name="video" ctl={ctl} onRefresh={load} />
        {err && <div className="alert warn small" role="alert">{err}</div>}
        {st && (
          <>
            {st.checks && <ChecksList checks={st.checks} ctl={ctl} onRepair={async (id) => { await api.admin.repairFix(id); }} />}
            <p className="small" style={{ margin: "8px 0 0" }}>
              Идёт эфиров: <b>{st.running}</b> из {st.max_concurrent} · зрителей сейчас: <b>{st.viewers}</b> · нагрузка Egress: <b>{st.egress_cpu_load == null ? "нет данных" : `${st.egress_cpu_load}%`}</b> (предел {st.max_cpu_load}%) · площадок: идёт <b>{st.destinations.live ?? 0}</b>, с ошибкой <b>{st.destinations.failed ?? 0}</b> · записей MP4 идёт: <b>{st.recordings_running}</b> · сегментов HLS на диске: <b>{bytes(st.hls_bytes)}</b> · свободно на томе: <b>{st.free_bytes == null ? "нет данных" : bytes(st.free_bytes)}</b>
              <InfoTip doc={`${WIKI}Массовые-трансляции`}>Эфир ведёт та же служба Egress, что и видеозапись. Новый эфир не начинается при нехватке места или превышении предела, а идущие конференции от этого не страдают.</InfoTip>
              </p>
            {st.warnings.filter((w) => !st.checks?.some((c) => w.startsWith(c.title))).map((w) => <div key={w} className="alert warn small" role="alert">{w}</div>)}
            {st.active.length > 0 && (
              <table className="table small" style={{ marginTop: 8 }}>
                <thead><tr><th>Комната</th><th>Состояние</th><th>Качество</th><th>Зрителей</th><th>С</th><th>Запустил</th></tr></thead>
                <tbody>{st.active.map((a, i) => <tr key={i}><td>{a.room}</td><td>{a.status === "live" ? "идёт" : a.status === "starting" ? "запускается" : "заканчивается"}{a.hls && !a.hls_ready ? " (плейлист готовится)" : ""}</td><td>{a.quality}</td><td>{a.viewers}</td><td>{new Date(a.since).toLocaleTimeString("ru-RU")}</td><td>{a.by ?? "—"}</td></tr>)}</tbody>
              </table>)}
            {st.failed.length > 0 && <details style={{ marginTop: 8 }}><summary className="small">Последние прерванные эфиры ({st.failed.length})</summary>
              <ul className="small">{st.failed.map((f, i) => <li key={i}>{f.room}: {f.error ?? "причина не указана"} <span className="muted">({new Date(f.at).toLocaleString("ru-RU")})</span></li>)}</ul></details>}
          </>
        )}
      </div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row"><b>CDN</b><InfoTip doc={`${WIKI}CDN-для-массовых-мероприятий`}>CDN забирает файлы с этого сервера по тем же путям (/hls/…) и раздаёт зрителям, кэшируя сегменты и почти не кэшируя плейлист. Порядок: «Адрес доставки CDN» → «Раздавать HLS через CDN» → «Сохранить» → «Проверить CDN»; в комнате — «Раздавать через CDN».</InfoTip>
          <span className={`badge ${st?.cdn.enabled ? "ok" : ""}`}>{st ? (st.cdn.enabled ? "подключён" : "не подключён") : "…"}</span><div className="spacer" />
          <button className="btn mini" onClick={() => void checkCdn()} disabled={cdnBusy || !st?.cdn.base_url}>{cdnBusy ? "Проверка…" : "Проверить CDN"}</button></div>
        {cdnMsg && <div className={`alert ${cdnMsg.ok ? "ok" : "warn"} small`} role="status">{cdnMsg.text}</div>}
      </div>
      <div id="settings">
        <SettingsForm key="live" group="live" title="Настройки трансляций" fields={fields}
          intro="Зрители смотрят HLS, который nginx раздаёт прямо с этого сервера, без подключения к LiveKit. Эфир включается в комнате: тип «Трансляционная» или вкладка «Трансляция» в настройках комнаты." />
      </div>
    </>
  );
}
