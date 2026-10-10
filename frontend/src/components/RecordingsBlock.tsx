import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type MediaItem, type MeetingMedia } from "../api";
import { fmtTime } from "../mediaPlayerMath";
import { bytes, fmt } from "../util";
import { Icon } from "./Icons";
import type { PlayerItem } from "./MediaPlayer";

const MediaPlayer = lazy(() => import("./MediaPlayer"));       // плеер подгружается при первом открытии

const TITLE: Record<string, string> = { mix_video: "Видеозапись встречи", mix_audio: "Аудиозапись встречи", participant: "Запись участника" };

/** Блок «Записи» на странице завершённой встречи: общая запись (основной материал) и, для администратора, файлы участников. «Смотреть» и «Слушать» открывают плеер поверх страницы. */
export default function RecordingsBlock({ meetingId }: { meetingId: string }) {
  const [data, setData] = useState<MeetingMedia | null>(null);
  const [err, setErr] = useState("");
  const [play, setPlay] = useState<PlayerItem | null>(null);
  const load = useCallback(() => api.meetingMedia(meetingId).then((d) => { setData(d); setErr(""); }).catch((e) => setErr((e as ApiError).message)), [meetingId]);
  useEffect(() => { void load(); }, [load]);
  // пока общая запись формируется в фоне — обновляем список
  const processing = !!data?.mixes.some((m) => m.status === "processing");
  useEffect(() => { if (!processing) return; const t = window.setInterval(() => void load(), 8000); return () => window.clearInterval(t); }, [processing, load]);

  if (err) return null;                                  // записей нет или нет доступа — блок не показываем
  if (!data || (!data.mixes.length && !data.participants.length)) return null;
  const open = (m: MediaItem, title: string) => setPlay({ id: m.id, meetingId, title, hasVideo: m.has_video, durationS: m.duration_s ?? 0, canDownload: m.can_download });

  return (
    <div className="card rec-block" aria-label="Записи встречи">
      <h2>Записи</h2>
      {data.mixes.map((m) => (
        <div key={m.id} className="rec-row">
          <Icon name={m.has_video ? "video" : "mic"} size={20} />
          <div className="rec-info">
            <b>{TITLE[m.kind] ?? "Запись"}</b>
            <span className="muted small">
              {m.status === "processing" ? "формируется…" : m.status === "failed" ? `не удалось сформировать: ${m.error ?? "ошибка"}` : m.file_state === "missing" ? "файл удалён из хранилища"
                : `${fmtTime(m.duration_s ?? 0)} · ${bytes(m.size_bytes)} · ${m.has_video ? "MP4" : "M4A"}${m.started_at ? ` · с ${fmt(m.started_at)}` : ""}`}
            </span>
          </div>
          <button type="button" className="btn primary" disabled={m.status !== "ready" || m.file_state === "missing"} onClick={() => open(m, TITLE[m.kind] ?? "Запись")}>
            {m.has_video ? "Смотреть" : "Слушать"}
          </button>
        </div>
      ))}
      {data.participants.length > 0 && (
        <details className="rec-individual">
          <summary>Индивидуальные записи участников ({data.participants.length})</summary>
          {data.participants.map((p) => (
            <div key={p.id} className="rec-row small">
              <Icon name="mic" size={16} />
              <div className="rec-info"><span>{p.name}</span><span className="muted">{fmtTime(p.duration_s ?? 0)} · {bytes(p.size_bytes)}</span></div>
              <button type="button" className="btn mini" disabled={p.file_state === "missing"} onClick={() => open(p, p.name ?? "Запись участника")}>Слушать</button>
            </div>
          ))}
        </details>
      )}
      {play && <Suspense fallback={null}><MediaPlayer item={play} onClose={() => setPlay(null)} /></Suspense>}
    </div>
  );
}
