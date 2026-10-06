import { useEffect, useRef, useState } from "react";
import { api, type Segment } from "../api";
import { LiveSocket, type LiveEvent } from "../liveSocket";
import { formatTime, mergeSegment, mergeSegments } from "../transcript";

interface Props {
  meetingId: string;
  enabled: boolean;
  onMeetingEnded?: () => void;
  onEvent?: (e: LiveEvent) => void;
}

/** Живая транскрибация: история встречи + новые реплики по WebSocket (без перезагрузки страницы). */
export default function TranscriptPanel({ meetingId, enabled, onMeetingEnded, onEvent: forward }: Props) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [connected, setConnected] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  useEffect(() => {
    let cancelled = false;
    setSegments([]);
    const load = () => api.transcript(meetingId).then((t) => { if (!cancelled) setSegments((cur) => mergeSegments(cur, t.segments)); }).catch(() => undefined);
    const onEvent = (e: LiveEvent) => {
      if (e.type === "segment") setSegments((cur) => mergeSegment(cur, e.segment));
      else if (e.type === "meeting_ended") onMeetingEnded?.();
      forward?.(e);
    };
    const sock = new LiveSocket(meetingId, onEvent, (c) => { setConnected(c); if (c) load(); }); // после переподключения — догрузка пропущенного
    sock.start();
    return () => { cancelled = true; sock.stop(); };
  }, [meetingId, onMeetingEnded, forward]);

  useEffect(() => {
    const el = boxRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [segments]);

  const onScroll = () => {
    const el = boxRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  return (
    <aside className="transcript card">
      <div className="row">
        <h2>Транскрипция</h2>
        <span className={`dot ${connected ? "ok" : "off"}`} title={connected ? "Подключено" : "Нет связи — переподключение…"} />
      </div>
      {!enabled && <p className="muted">В этой комнате транскрибация отключена.</p>}
      <div className="transcript-list" ref={boxRef} onScroll={onScroll} aria-live="polite">
        {segments.map((s) => (
          <p key={s.uid} className="utt">
            <span className="time">{formatTime(s.started_at)}</span>
            <strong>{s.display_name}</strong>
            <span>{s.text}</span>
          </p>
        ))}
        {enabled && segments.length === 0 && <p className="muted">Реплики появятся здесь по мере разговора.</p>}
      </div>
    </aside>
  );
}
