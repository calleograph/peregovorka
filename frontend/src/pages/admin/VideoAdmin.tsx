import { useCallback, useEffect, useState } from "react";
import { api, type VideoStatus } from "../../api";
import { bytes } from "../../util";
import InfoTip from "../../components/InfoTip";
import SettingsForm, { type Field } from "./SettingsForm";
import { ChecksList, ComponentCard, useComponents, WIKI } from "./ServerComponents";

/** «Видеозапись»: служба на сервере (включение из панели), проверки по цепочке, сводка файлов и настройки, включая хранилище видеозаписей. */
export default function VideoAdmin({ fields }: { fields: Field[] }) {
  const [st, setSt] = useState<VideoStatus | null>(null);
  const [err, setErr] = useState("");
  const ctl = useComponents();
  const load = useCallback(() => { api.adminVideoStatus().then((s) => { setSt(s); setErr(""); }).catch((e) => setErr((e as Error).message || "Не удалось получить состояние")); }, []);
  const busy = !!ctl.info?.helper.busy;
  useEffect(() => { load(); const t = window.setInterval(load, busy ? 4000 : 15000); return () => window.clearInterval(t); }, [load, busy]);
  return (
    <>
      <div className="card" style={{ marginBottom: 12 }}>
        <ComponentCard name="video" ctl={ctl} />
        {err && <div className="alert warn small" role="alert">{err}</div>}
        {st?.checks && <ChecksList checks={st.checks} ctl={ctl} onRepair={async (id) => { await api.admin.repairFix(id); }} />}
        {st && (
          <p className="small" style={{ margin: "8px 0 0" }}>
            Идёт записей: <b>{st.running}</b> из {st.max_concurrent} · готовых: <b>{st.files.ready}</b> · с ошибкой: <b>{st.files.failed}</b> · занято: <b>{bytes(st.files.bytes)}</b>
            {st.quota_gb ? ` из ${st.quota_gb} ГБ` : ""}
            <InfoTip doc={`${WIKI}Как-записать-видео-встречи`}>Один MP4 со всей встречей: изображение и звук всех участников, показ экрана — на основном месте. Режим выбирается в настройках каждой комнаты (вкладка «Встреча и запись»). Служба записи общая с трансляциями.</InfoTip>
            <button className="btn mini ghost" style={{ marginLeft: 8 }} onClick={load}>Обновить</button>
          </p>
        )}
        {st?.warnings.filter((w) => !st.checks?.some((c) => w.startsWith(c.title))).map((w) => <div key={w} className="alert warn small" role="alert">{w}</div>)}
      </div>
      <div id="settings">
        <SettingsForm key="video" group="video" title="Настройки видеозаписи" fields={fields} />
      </div>
    </>
  );
}
