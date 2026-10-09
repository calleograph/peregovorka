import { type KeyboardEvent, type PointerEvent, useEffect, useRef, useState } from "react";
import { api, type Segment } from "../api";
import { LiveBus, LiveSocket, type LiveEvent, type SocketStatus } from "../liveSocket";
import { formatTime, mergeSegment, mergeSegments } from "../transcript";
import { chatSoundEnabled, playChatSound, setChatSoundEnabled } from "../chatSound";
import { type Collapsed, flexFor, loadRatio, ratioFromPointer, saveRatio, clampRatio } from "../panelSplit";
import ChatPanel from "./ChatPanel";
import { Icon } from "./Icons";

interface Props {
  meetingId: string;
  enabled: boolean;
  /** Транскрибация приостановлена руководителем (звонок и запись звука продолжаются). */
  paused?: boolean;
  /** Вложения в чат разрешены. */
  canAttach?: boolean;
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

/** Широкий экран: транскрипция и чат видны одновременно (верх/низ). Узкий — прежние вкладки. */
function useWide(): boolean {
  const q = "(min-width: 861px)";
  const [w, setW] = useState(() => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(q).matches : true));
  useEffect(() => {
    if (!window.matchMedia) return;
    const m = window.matchMedia(q);
    const on = () => setW(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return w;
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
export default function TranscriptPanel({ meetingId, enabled, paused = false, canAttach = true, asrReady = true, asrLost = false, collapsed = false, onToggleCollapsed, onMeetingEnded, onEvent: forward, onStatus, bus: busProp, guestToken = null, selfName, openChatSignal }: Props) {
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
  const wide = useWide();
  const split = wide && !guest;
  const [ratio, setRatio] = useState(loadRatio);
  const [part, setPart] = useState<Collapsed>(null);
  const [soundOn, setSoundOn] = useState(chatSoundEnabled);
  const bodyRef = useRef<HTMLDivElement>(null);
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
      <aside className="transcript collapsed card" aria-label={guest ? "Чат (свёрнут)" : "Транскрипция и чат (свёрнуты)"}>
        {/* вся полоса — одна большая кнопка «развернуть»: раньше крошечная стрелка была почти незаметна */}
        <button type="button" className="rail-btn" onClick={onToggleCollapsed} title={guest ? "Развернуть чат" : "Развернуть транскрипцию и чат"} aria-label={guest ? "Развернуть чат" : "Развернуть транскрипцию и чат"}>
          <span className="rail-chevron" aria-hidden><Icon name="chevronL" size={20} /></span>
          {unread > 0 && <span className="unread-dot" aria-label={`Непрочитанных сообщений: ${unread}`}>{unread}</span>}
          <span className="rail-label">{guest ? "Чат" : "Транскрипция и чат"}{!guest && segments.length ? ` · ${segments.length}` : ""}</span>
          <span className={`dot ${st.cls}`} title={st.text} />
        </button>
      </aside>
    );
  }
  const head = (
    <div className="row side-head">
      {split ? <b className="side-title">Транскрипция и чат</b> : (
        <div className="tabs side-tabs" role="tablist">
          {!guest && <button role="tab" aria-selected={tab === "transcript"} className={`tab ${tab === "transcript" ? "active" : ""}`} onClick={() => setTab("transcript")}>Транскрипция</button>}
          <button role="tab" aria-selected={tab === "chat"} className={`tab ${tab === "chat" ? "active" : ""}`} onClick={() => setTab("chat")}>
            Чат{unread > 0 && <span className="unread-dot" aria-label={`Непрочитанных сообщений: ${unread}`}>{unread}</span>}
          </button>
        </div>
      )}
      <span className={`dot ${st.cls}`} title={st.text} />
      <div className="spacer" />
      {onToggleCollapsed && <button type="button" className="icon-btn" onClick={onToggleCollapsed} title="Свернуть панель" aria-label="Свернуть панель"><Icon name="chevronR" size={18} /></button>}
    </div>
  );
  const transcriptBody = (
    <>
      {!enabled && <p className="muted">В этой комнате транскрибация отключена.</p>}
      {enabled && paused && <div className="alert info" role="status">Транскрибация приостановлена руководителем. Звонок и запись звука продолжаются; реплики за это время в стенограмму не попадут.</div>}
      {enabled && !paused && !asrReady && <div className="alert info" role="status">{asrLost ? "Транскрибация временно недоступна — звонок продолжается. Реплики вернутся, когда сервис распознавания восстановится." : "Транскрибация запускается — звонок уже работает. Реплики появятся, как только сервис распознавания будет готов."}</div>}
      <div className="transcript-list" ref={boxRef} onScroll={onScroll} aria-live="polite">
        {segments.map((s) => (
          <p key={s.uid} className="utt">
            <span className="time">{formatTime(s.started_at)}</span>
            <strong>{s.display_name}</strong>
            <span>{s.text}</span>
          </p>
        ))}
        {enabled && asrReady && !paused && segments.length === 0 && <p className="muted">Реплики появятся здесь по мере разговора.</p>}
      </div>
    </>
  );

  if (!split) {
    return (
      <aside className="transcript card">
        {head}
        {st.cls !== "ok" && <div className="muted small">{st.text}</div>}
        <ChatPanel meetingId={meetingId} bus={bus} visible={tab === "chat"} selfName={selfName} onUnread={setUnread} canAttach={canAttach} />
        {tab === "transcript" && !guest && transcriptBody}
      </aside>
    );
  }

  const onDividerDown = (e: PointerEvent<HTMLDivElement>) => {
    const box = bodyRef.current;
    if (!box) return;
    e.preventDefault();
    (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
    const move = (ev: globalThis.PointerEvent) => { const r = box.getBoundingClientRect(); setRatio(ratioFromPointer(ev.clientY, r.top, r.height)); };
    const up = (ev: globalThis.PointerEvent) => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      const r = box.getBoundingClientRect(); saveRatio(ratioFromPointer(ev.clientY, r.top, r.height));
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };
  const onDividerKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const d = e.key === "ArrowUp" ? -0.05 : e.key === "ArrowDown" ? 0.05 : 0;
    if (!d) return;
    e.preventDefault();
    const r = clampRatio(ratio + d); setRatio(r); saveRatio(r);
  };
  const toggle = (which: "top" | "bottom") => setPart((cur) => (cur === which ? null : which));
  const paneBtns = (which: "top" | "bottom", what: string) => (
    <span className="pane-btns">
      <button type="button" className="icon-btn" onClick={() => toggle(which)} aria-pressed={part === which}
              title={part === which ? `Развернуть: ${what}` : `Свернуть: ${what}`} aria-label={part === which ? `Развернуть: ${what}` : `Свернуть: ${what}`}>
        <span style={{ display: "inline-flex", transform: part === which ? "rotate(-90deg)" : "none", transition: "transform .2s" }}><Icon name="chevronD" size={16} /></span></button>
      <button type="button" className="icon-btn" onClick={() => toggle(which === "top" ? "bottom" : "top")} aria-pressed={part === (which === "top" ? "bottom" : "top")}
              title={`На всю панель: ${what}`} aria-label={`На всю панель: ${what}`}><Icon name="expandAll" size={15} /></button>
    </span>
  );
  return (
    <aside className="transcript card split">
      {head}
      {st.cls !== "ok" && <div className="muted small">{st.text}</div>}
      <div className="tp-body" ref={bodyRef}>
        <section className={`tp-pane tp-top ${part === "top" ? "min" : ""}`} style={{ flex: flexFor("top", ratio, part) }} aria-label="Транскрипция">
          <div className="tp-head"><b>Транскрипция</b>{segments.length ? <span className="muted small">{segments.length}</span> : null}<span className="spacer" />{paneBtns("top", "транскрипцию")}</div>
          {part !== "top" && <div className="tp-content">{transcriptBody}</div>}
        </section>
        {part === null && <div className="tp-divider" role="separator" aria-orientation="horizontal" aria-label="Изменить высоту транскрипции и чата (стрелки вверх/вниз)"
                               aria-valuenow={Math.round(ratio * 100)} aria-valuemin={15} aria-valuemax={85} tabIndex={0} onPointerDown={onDividerDown} onKeyDown={onDividerKey}
                               onDoubleClick={() => { setRatio(0.65); saveRatio(0.65); }} title="Потяните, чтобы изменить высоту; двойной щелчок — по умолчанию" />}
        <section className={`tp-pane tp-bottom ${part === "bottom" ? "min" : ""}`} style={{ flex: flexFor("bottom", ratio, part) }} aria-label="Чат">
          <div className="tp-head"><b>Чат</b>{unread > 0 && part === "bottom" && <span className="unread-dot" aria-label={`Непрочитанных сообщений: ${unread}`}>{unread}</span>}<span className="spacer" />
            <button type="button" className="icon-btn" aria-pressed={soundOn} onClick={() => { setChatSoundEnabled(!soundOn); setSoundOn(!soundOn); if (!soundOn) playChatSound(true); }}
                    title={soundOn ? "Звук новых сообщений включён (нажмите, чтобы выключить)" : "Звук новых сообщений выключен"} aria-label="Звук новых сообщений">{soundOn ? "🔔" : "🔕"}</button>
            {paneBtns("bottom", "чат")}</div>
          <div className="tp-content" hidden={part === "bottom"}>
            <ChatPanel meetingId={meetingId} bus={bus} visible={part !== "bottom"} selfName={selfName} onUnread={setUnread} canAttach={canAttach} />
          </div>
        </section>
      </div>
    </aside>
  );
}
