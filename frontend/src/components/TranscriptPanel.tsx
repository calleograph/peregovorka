import { useEffect, useRef, useState } from "react";
import { api, type Segment } from "../api";
import { LiveBus, LiveSocket, type LiveEvent, type SocketStatus } from "../liveSocket";
import { formatTime, mergeSegment, mergeSegments } from "../transcript";
import ChatPanel from "./ChatPanel";

interface Props {
  meetingId: string;
  enabled: boolean;
  /** Транскрибация (ASR) готова к работе. Вход в комнату от неё не зависит. */
  asrReady?: boolean;
  /** Транскрибация была готова и пропала (ASR перегружен/перезапускается). Звонок продолжается. */
  asrLost?: boolean;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onMeetingEnded?: () => void;
  onEvent?: (e: LiveEvent) => void;
  onStatus?: (s: SocketStatus) => void;
  /** Общий поток событий комнаты (чат, доска): сокет у панели один. */
  bus?: LiveBus;
  /** Гость: стенограммы не получает (только чат), сокет подтверждается токеном гостя. */
  guestToken?: string | null;
  selfName?: string;
  /** Просьба открыть вкладку чата (например, из кнопки управления). Меняется — вкладка переключается. */
  openChatSignal?: number;
}

type Tab = "transcript" | "chat";

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
export default function TranscriptPanel({ meetingId, enabled, asrReady = true, asrLost = false, collapsed = false, onToggleCollapsed, onMeetingEnded, onEvent: forward, onStatus, bus: busProp, guestToken = null, selfName, openChatSignal }: Props) {
  const guest = !!guestToken;
  const [tab, setTab] = useState<Tab>(guest ? "chat" : "transcript");
  const [unread, setUnread] = useState(0);
  const ownBus = useRef(new LiveBus());
  const bus = busProp ?? ownBus.current;
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
    const load = () => {
      bus.resync(); // чат и доска тоже догружают пропущенное
      if (guest) return Promise.resolve(); // стенограмма гостю не отдаётся
      return api.transcript(meetingId).then((t) => { if (!cancelled) setSegments((cur) => mergeSegments(cur, t.segments)); }).catch(() => undefined);
    };
    const sock = new LiveSocket(meetingId, (e) => {
      if (e.type === "segment") setSegments((cur) => mergeSegment(cur, e.segment));
      else if (e.type === "meeting_ended") cb.current.onMeetingEnded?.();
      bus.emit(e);
      cb.current.forward?.(e);
    }, (s) => { setStatus(s); cb.current.onStatus?.(s); }, () => { void load(); }, guestToken); // после (пере)подключения — догрузка пропущенного
    sock.start();
    if (!guest) void load();
    return () => { cancelled = true; sock.stop(); };
  }, [meetingId, bus, guest, guestToken]);

  useEffect(() => { if (openChatSignal) { setTab("chat"); if (collapsed) onToggleCollapsed?.(); } }, [openChatSignal]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (status.state !== "reconnecting") return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [status.state]);

  useEffect(() => {
    const el = boxRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [segments, collapsed, tab]);

  const onScroll = () => {
    const el = boxRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const st = statusText(status, now);
  if (collapsed) {
    return (
      <aside className="transcript collapsed card" aria-label="Транскрипция (свёрнута)">
        <button className="btn mini" onClick={onToggleCollapsed} title="Развернуть транскрипцию">◂</button>
        <span className="rail-label">{guest ? "Чат" : "Транскрипция и чат"}{unread ? ` · новых: ${unread}` : segments.length && !guest ? ` · ${segments.length}` : ""}</span>
        {unread > 0 && <span className="unread-dot" aria-label={`Непрочитанных сообщений: ${unread}`}>{unread}</span>}
        <span className={`dot ${st.cls}`} title={st.text} />
      </aside>
    );
  }
  return (
    <aside className="transcript card">
      <div className="row side-head">
        <div className="tabs side-tabs" role="tablist">
          {!guest && <button role="tab" aria-selected={tab === "transcript"} className={`tab ${tab === "transcript" ? "active" : ""}`} onClick={() => setTab("transcript")}>Транскрипция</button>}
          <button role="tab" aria-selected={tab === "chat"} className={`tab ${tab === "chat" ? "active" : ""}`} onClick={() => setTab("chat")}>
            Чат{unread > 0 && <span className="unread-dot" aria-label={`Непрочитанных сообщений: ${unread}`}>{unread}</span>}
          </button>
        </div>
        <span className={`dot ${st.cls}`} title={st.text} />
        <div className="spacer" />
        {onToggleCollapsed && <button className="btn mini" onClick={onToggleCollapsed} title="Свернуть панель">▸</button>}
      </div>
      {st.cls !== "ok" && <div className="muted small">{st.text}</div>}
      <ChatPanel meetingId={meetingId} bus={bus} visible={tab === "chat"} selfName={selfName} onUnread={setUnread} />
      {tab === "transcript" && !guest && <>
      {!enabled && <p className="muted">В этой комнате транскрибация отключена.</p>}
      {enabled && !asrReady && <div className="alert info" role="status">{asrLost ? "Транскрибация временно недоступна — звонок продолжается. Реплики вернутся, когда сервис распознавания восстановится." : "Транскрибация запускается — звонок уже работает. Реплики появятся, как только сервис распознавания будет готов."}</div>}
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
      </>}
    </aside>
  );
}
