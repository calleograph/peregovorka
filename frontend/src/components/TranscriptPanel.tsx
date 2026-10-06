import { useEffect, useRef, useState } from "react";
import { api, type Segment } from "../api";
import { LiveSocket, type LiveEvent, type SocketStatus } from "../liveSocket";
import { formatTime, mergeSegment, mergeSegments } from "../transcript";

interface Props {
  meetingId: string;
  enabled: boolean;
  /** Транскрибация (ASR) готова к работе. Вход в комнату от неё не зависит. */
  asrReady?: boolean;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onMeetingEnded?: () => void;
  onEvent?: (e: LiveEvent) => void;
  onStatus?: (s: SocketStatus) => void;
}

function statusText(s: SocketStatus, now: number): { text: string; cls: "ok" | "off" | "bad" } {
  switch (s.state) {
    case "online": return { text: "Подключено", cls: "ok" };
    case "connecting": return { text: "Подключение…", cls: "off" };
    case "denied": return { text: s.message ?? "Нет доступа", cls: "bad" };
    default: {
      const left = s.retryAt ? Math.max(0, Math.ceil((s.retryAt - now) / 1000)) : 0;
      return { text: `Нет связи — переподключение${s.attempt ? ` (попытка ${s.attempt}${left ? `, через ${left} с` : ""})` : ""}…`, cls: "bad" };
    }
  }
}

/** Живая транскрибация: история встречи + новые реплики по WebSocket (без перезагрузки страницы). */
export default function TranscriptPanel({ meetingId, enabled, asrReady = true, collapsed = false, onToggleCollapsed, onMeetingEnded, onEvent: forward, onStatus }: Props) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [status, setStatus] = useState<SocketStatus>({ state: "connecting", attempt: 0 });
  const [now, setNow] = useState(Date.now());
  const boxRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const cb = useRef({ onMeetingEnded, forward, onStatus });
  cb.current = { onMeetingEnded, forward, onStatus };

  useEffect(() => {
    let cancelled = false;
    setSegments([]);
    const load = () => api.transcript(meetingId).then((t) => { if (!cancelled) setSegments((cur) => mergeSegments(cur, t.segments)); }).catch(() => undefined);
    const sock = new LiveSocket(meetingId, (e) => {
      if (e.type === "segment") setSegments((cur) => mergeSegment(cur, e.segment));
      else if (e.type === "meeting_ended") cb.current.onMeetingEnded?.();
      cb.current.forward?.(e);
    }, (s) => { setStatus(s); cb.current.onStatus?.(s); }, load); // после (пере)подключения — догрузка пропущенного
    sock.start();
    void load();
    return () => { cancelled = true; sock.stop(); };
  }, [meetingId]);

  useEffect(() => {
    if (status.state !== "reconnecting") return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [status.state]);

  useEffect(() => {
    const el = boxRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [segments, collapsed]);

  const onScroll = () => {
    const el = boxRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const st = statusText(status, now);
  if (collapsed) {
    return (
      <aside className="transcript collapsed card" aria-label="Транскрипция (свёрнута)">
        <button className="btn mini" onClick={onToggleCollapsed} title="Развернуть транскрипцию">◂</button>
        <span className="rail-label">Транскрипция{segments.length ? ` · ${segments.length}` : ""}</span>
        <span className={`dot ${st.cls}`} title={st.text} />
      </aside>
    );
  }
  return (
    <aside className="transcript card">
      <div className="row">
        <h2>Транскрипция</h2>
        <span className={`dot ${st.cls}`} title={st.text} />
        <span className="muted small">{st.text}</span>
        <div className="spacer" />
        {onToggleCollapsed && <button className="btn mini" onClick={onToggleCollapsed} title="Свернуть панель">▸</button>}
      </div>
      {!enabled && <p className="muted">В этой комнате транскрибация отключена.</p>}
      {enabled && !asrReady && <div className="alert info" role="status">Транскрибация запускается — звонок уже работает. Реплики появятся, как только сервис распознавания будет готов.</div>}
      <div className="transcript-list" ref={boxRef} onScroll={onScroll} aria-live="polite">
        {segments.map((s) => (
          <p key={s.uid} className="utt">
            <span className="time">{formatTime(s.started_at)}</span>
            <strong>{s.display_name}</strong>
            <span>{s.text}</span>
          </p>
        ))}
        {enabled && asrReady && segments.length === 0 && <p className="muted">Реплики появятся здесь по мере разговора.</p>}
      </div>
    </aside>
  );
}
