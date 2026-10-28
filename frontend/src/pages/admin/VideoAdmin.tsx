import { useCallback, useEffect, useState } from "react";
import { api, type VideoStatus } from "../../api";
import { bytes } from "../../util";
import SettingsForm, { type Field } from "./SettingsForm";

/** «Видеозапись»: состояние (идёт записей, занято места, предупреждения) и настройки. Службу Egress ставит администратор сервера: sudo scripts/video.sh enable. */
export default function VideoAdmin({ fields }: { fields: Field[] }) {
  const [st, setSt] = useState<VideoStatus | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => { api.adminVideoStatus().then((s) => { setSt(s); setErr(""); }).catch((e) => setErr((e as Error).message || "Не удалось получить состояние")); }, []);
  useEffect(() => { load(); const t = window.setInterval(load, 15000); return () => window.clearInterval(t); }, [load]);
  return (
    <>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row"><b>Состояние</b><span className={`badge ${st?.enabled ? "ok" : ""}`}>{st ? (st.enabled ? "включена" : "выключена") : "…"}</span>
          <div className="spacer" /><button className="btn mini" onClick={load}>Обновить</button></div>
        {err && <div className="alert warn small" role="alert">{err}</div>}
        {st && (
          <>
            <p className="small" style={{ margin: "8px 0 0" }}>
              Идёт записей: <b>{st.running}</b> из {st.max_concurrent} · готовых файлов: <b>{st.files.ready}</b> · с ошибкой: <b>{st.files.failed}</b> · занято: <b>{bytes(st.files.bytes)}</b>
              {st.quota_gb ? ` из ${st.quota_gb} ГБ` : ""} · свободно на томе: <b>{st.free_bytes == null ? "нет данных" : bytes(st.free_bytes)}</b>
            </p>
            {st.warnings.map((w) => <div key={w} className="alert warn small" role="alert">{w}</div>)}
          </>
        )}
        <p className="muted small" style={{ marginBottom: 0 }}>Службу записи (LiveKit Egress, браузер внутри контейнера) устанавливает администратор сервера: <code>sudo scripts/video.sh enable</code>; состояние службы — <code>scripts/video.sh status</code>.
          Если служба не установлена, кнопка записи в комнате сообщит об этом понятным текстом.</p>
      </div>
      <SettingsForm key="video" group="video" title="Видеозапись встреч" fields={fields}
        intro="Один MP4 со всей встречей (изображение и звук всех участников; при показе экрана он занимает основное место). Режим выбирается в настройках каждой комнаты; по умолчанию видеозапись выключена." />
    </>
  );
}
