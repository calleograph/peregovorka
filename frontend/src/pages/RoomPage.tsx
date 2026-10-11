import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ConnectionState, DisconnectReason, LogLevel, Participant, Room as LkRoom, RoomEvent, Track, createLocalAudioTrack, setLogLevel, type LocalAudioTrack, type LocalVideoTrack } from "livekit-client";
import { describeConnection, describeProbe, failStage, probeSignal, redactSecrets, safeUrl, type FailStage, type SignalProbe } from "../lkDiag";
import { api, ApiError, leaveOnUnload, type GuestJoinInfo, type HandInfo, type JoinInfo, type Room } from "../api";
import Whiteboard from "../board/Whiteboard";
import PreJoin from "../components/PreJoin";
import ParticipantCardDialog from "../components/room/ParticipantCardDialog";
import { applyLocalMute, loadLocalMuted, saveLocalMuted, toggleLocalMute } from "../localMute";
import { handSoundEnabled, playHandSound, setHandSoundEnabled } from "../handSound";
import ConnectProgress from "../components/room/ConnectProgress";
import { ParticipantTile, ScreenStage, moderationItems, pipSupported, toggleFullscreen, togglePip, type PView, type TileActions } from "../components/room/Tiles";
import StageView from "../components/room/StageView";
import { useContextMenu, type MenuItem } from "../components/ContextMenu";
import { useMeetingStage, type StageSource } from "../stage/useMeetingStage";
import { LAYOUTS, MAX_PINS, MOBILE_W, stripOrder, type StageItem } from "../stage/stageModel";
import { Icon } from "../components/Icons";
import RoundButton from "../components/room/RoundButton";
import { ConfirmDialog } from "../components/Dialogs";
import DevicePanel from "../components/DevicePanel";
import TranscriptPanel from "../components/TranscriptPanel";
import { FreezeDetector, JoinTimeline, RateMeter, metricsBody, newInstanceId, reportEvent, reportRoomPhase, reportScreenPhase, sampleRoom, type RoomPhase, type ScreenPhase, type Snapshot, type Stage } from "../diagnostics";
import { buildRoomOptions } from "../roomOptions";
import { captureOptions, loadMicPrefs, saveMicPrefs, type MicPrefs } from "../micPrefs";
import { collectAll } from "../clientInfo";
import type { LiveEvent, SocketStatus } from "../liveSocket";
import { LiveBus, backoffDelay } from "../liveSocket";
import { copyText, fileBase as fileBaseName } from "../util";
import { tileName } from "../phone";
import { setActiveMeeting } from "../activeMeeting";
import { setPreJoin, takePreJoin, type PreJoin as PreJoinHw } from "../prejoin";
import { describeMediaError, isDeviceBusyError, isTransientConnectError, SCREEN_STOP_TEXT, type MediaAction, type ScreenStopReason } from "../mediaErrors";
import { PROFILE_LABELS, PROFILE_ORDER, applyProfileLive, ecoReport, loadProfile, measureScreen, saveProfile, screenShareOptions, type ScreenProfile } from "../screenShare";
import AudiencePanel from "../components/room/AudiencePanel";
import { audienceLabel, sameList, splitStage } from "../presentation";

// диалог настроек нужен только руководителю — грузится по требованию (вместе с общим для администрирования выбором доступа)
const RoomManageDialog = lazy(() => import("../components/RoomManageDialog"));
const MeetingSettingsDialog = lazy(() => import("../components/MeetingSettingsDialog"));
const PhoneDialog = lazy(() => import("../components/PhoneDialog"));

type CtlKey = "mic" | "cam" | "screen" | "rec" | "tr" | "device" | "audio" | "general";
type CtlErrors = Partial<Record<CtlKey, string>>;

// Библиотека звонков на уровне info пишет в консоль адрес подключения целиком (с токеном доступа и большим join_request) — оставляем только предупреждения.
setLogLevel(LogLevel.warn);

const ALL_SOURCES = ["microphone", "camera", "screen_share", "screen_share_audio"];
const MAX_REJOIN = 6;
/** Корзина участника 0..19 по идентификатору: стабильна, не требует случайных чисел. */
const statBucket = (identity: string): number => { let h = 0; for (let i = 0; i < identity.length; i++) h = (h * 31 + identity.charCodeAt(i)) >>> 0; return h % 20; };
const REFRESH_MS = 60;         // события звонка (вход, выход, дорожки, говорящий) сливаются в одно обновление списка: при сотнях участников — не по перерисовке на каждое
const PVIEW_KEYS = ["identity", "name", "local", "mic", "cam", "screen", "speaking", "participant"] as const;
const MAX_CONNECT_TRIES = 3;   // первое подключение: до 3 попыток при сетевых/ICE-сбоях
const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));
const TW_MIN = 240, TW_MAX = 760, TW_DEFAULT = 300;
const lsGet = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };
const clamp = (n: number, a: number, b: number) => Math.min(b, Math.max(a, n));

/** Ошибка действия — рядом с той кнопкой, которая её вызвала. */
function Ctl({ error, onClose, children }: { error?: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="ctl">
      {children}
      {error && <div className="ctl-err" role="alert"><span>{error}</span><button className="x" onClick={onClose} aria-label="Закрыть сообщение">×</button></div>}
    </div>
  );
}

/** Гостевой вход: сессия уже создана страницей гостя (имя, проверка оборудования); здесь — сама комната без административных функций. */
export interface GuestSession { info: GuestJoinInfo; onLeft: (why: "left" | "ended") => void }

export default function RoomPage({ guest, selfName, roomIdOverride, roomInfo }: { guest?: GuestSession; selfName?: string; roomIdOverride?: string; roomInfo?: Room | null }) {
  const { roomId: routeRoomId = "" } = useParams();
  // адрес в строке — технический идентификатор комнаты; для запросов к серверу RoomRoute передаёт её UUID
  const roomId = guest ? guest.info.room.id : (roomIdOverride ?? routeRoomId);
  const navigate = useNavigate();
  const bus = useMemo(() => new LiveBus(), []);
  const [boardMounted, setBoardMounted] = useState(false);
  const [boardOpen, setBoardOpen] = useState(false);
  const [boardNews, setBoardNews] = useState<string | null>(null);
  const [chatSignal, setChatSignal] = useState(0);
  const firstJoinRef = useRef(true);
  const guestRef = useRef(guest);
  guestRef.current = guest;
  const [join, setJoin] = useState<JoinInfo | null>(null);
  const [needPassword, setNeedPassword] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ended, setEnded] = useState(false);
  const [recording, setRecording] = useState(false);          // идёт запись аудио
  const [transcribing, setTranscribing] = useState(true);     // идёт транскрибация (по умолчанию всегда)
  const [sources, setSources] = useState<string[]>(ALL_SOURCES);   // что разрешено публиковать (сервер выдаёт в токене)
  const [hands, setHands] = useState<HandInfo[]>([]);                      // очередь поднятых рук (раньше поднявший — выше)
  // «Заглушить для себя»: только на этом устройстве; не серверный mute, права не нужны; держится до конца встречи (в том числе при переподключении)
  const [localMuted, setLocalMuted] = useState<Set<string>>(() => new Set());
  const localMutedRef = useRef<Set<string>>(localMuted);
  const toggleLocalMuted = useCallback((identity: string) => {
    const next = toggleLocalMute(localMutedRef.current, identity);
    localMutedRef.current = next; setLocalMuted(next);
    applyLocalMute(audioBox.current, next);
    if (meetingRef.current) saveLocalMuted(meetingRef.current, next);
  }, []);
  const meetingId = join?.meeting_id;
  useEffect(() => {
    if (!meetingId) return;
    const saved = loadLocalMuted(meetingId);                  // после перезагрузки страницы во время встречи заглушённые остаются заглушёнными
    localMutedRef.current = saved; setLocalMuted(saved); applyLocalMute(audioBox.current, saved);
  }, [meetingId]);
  const [cardOf, setCardOf] = useState<{ identity: string; name: string; role?: string } | null>(null);
  const [handSound, setHandSound] = useState(handSoundEnabled);
  const floorRevokedAt = useRef(0);        // когда у нас забрали слово: короткое отключение сервером звонков после этого — штатное, комната входит заново зрителем
  const [floorIds, setFloorIds] = useState<Set<string>>(() => new Set());   // кому сейчас дано слово
  const [leaderIds, setLeaderIds] = useState<Set<string>>(() => new Set());
  const [canBoard, setCanBoard] = useState(true);
  const [canViewBoard, setCanViewBoard] = useState(true);       // при уровне «доска только у руководителей» остальные её не видят
  const [boardFocus, setBoardFocus] = useState(false);       // личный режим «Развернуть доску»: вся площадь окна, правая панель скрыта (панель остаётся смонтированной — канал событий не рвётся)
  const [devPop, setDevPop] = useState<{ left: number; bottom: number } | null>(null);
  const [manageOpen, setManageOpen] = useState(false);
  const [meetingSettingsOpen, setMeetingSettingsOpen] = useState(false);
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [asrReady, setAsrReady] = useState(true);
  const [participants, setParticipants] = useState<PView[]>([]);
  // Аватарки участников: один запрос при входе и ещё один, когда в комнате появляется кто-то новый (без постоянных опросов); сбой не мешает комнате — остаются инициалы.
  // Все хуки комнаты обязаны стоять ВЫШЕ ранних return (join ещё null при первом рисовании), иначе React падает: «rendered more hooks than during the previous render».
  const [avatars, setAvatars] = useState<Record<string, string>>({});
  const presentationRoom = !!join?.client.presentation;
  const amLeader = !!join?.client.can_manage;
  // Сцена и список зрителей: в презентационной комнате плитки только у руководителей, тех, кому дали слово, и у тех, кто публикует; остальные — строки списка.
  const split = useMemo(() => splitStage(participants.map((p) => ({ ...p, floor: floorIds.has(p.identity), leader: leaderIds.has(p.identity) || (p.local && amLeader) })), presentationRoom),
                        [participants, floorIds, leaderIds, presentationRoom, amLeader]);
  const audienceView = useMemo(() => {
    const order = new Map(hands.map((h, i) => [h.identity, i + 1] as const));
    return split.audience.map((p) => ({ ...p, hand: order.has(p.identity), handOrder: order.get(p.identity) })) as PView[];
  }, [split, hands]);
  const [audOpen, setAudOpen] = useState(false);
  const avatarKey = useMemo(() => split.stage.map((p) => p.identity).sort().join(","), [split]);        // аватарки нужны тем, у кого есть плитка: приход зрителя запрос не вызывает
  useEffect(() => {
    if (guest || !meetingId) return;
    const t = window.setTimeout(() => { void api.meetingAvatars(meetingId).then(setAvatars).catch(() => undefined); }, 600);
    return () => window.clearTimeout(t);
  }, [guest, meetingId, avatarKey]);
  const [state, setState] = useState<ConnectionState>(ConnectionState.Disconnected);
  const [stage, setStage] = useState<Stage>("prepare");
  const [, setTick] = useState(0);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [ctlErr, setCtlErr] = useState<CtlErrors>({});
  const [withAudio, setWithAudio] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [rejoin, setRejoin] = useState<{ attempt: number } | null>(null);
  const [, setSocket] = useState<SocketStatus>({ state: "connecting", attempt: 0 });
  const [, setSnapshot] = useState<Snapshot | null>(null);          // статистика уходит в журнал сервера; в комнате её не показываем — диагностика во «Встречи» администратора
  const [, setLog] = useState<string[]>([]);
  const [tw, setTw] = useState(() => clamp(Number(lsGet("room.tw")) || TW_DEFAULT, TW_MIN, TW_MAX));
  const [tCollapsed, setTCollapsed] = useState(() => { const v = lsGet("room.tcollapsed"); return v === null ? window.innerWidth <= 1000 : v === "1"; });     // на телефоне и узком окне панель по умолчанию свёрнута
  const [redirectIn, setRedirectIn] = useState<number | null>(null);
  const [asrLost, setAsrLost] = useState(false);
  const [, setRoomsCreated] = useState(0);
  const [, setInstance] = useState("");
  const [micPrefs, setMicPrefs] = useState<MicPrefs>(() => loadMicPrefs());
  const [micFail, setMicFail] = useState<"busy" | "denied" | "other" | null>(null);
  const [notice, setNotice] = useState<{ kind: "info" | "ok" | "warn"; text: string } | null>(null);
  const [welcome, setWelcome] = useState<string | null>(null);
  const lastMicToggle = useRef(0);
  const [connectTry, setConnectTry] = useState<{ attempt: number; max: number } | null>(null);
  // Сцена встречи: раскладка отделена от медиа (LiveKit) и от модерации (сервер); см. stage/stageModel.ts
  const [mobile, setMobile] = useState(() => typeof window.matchMedia === "function" && window.matchMedia(`(max-width: ${MOBILE_W}px)`).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(`(max-width: ${MOBILE_W}px)`);
    const on = () => setMobile(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  useEffect(() => {
    if (!join) return;
    document.body.classList.add("in-room");
    const hdr = document.querySelector("header.topbar");
    const set = () => { if (hdr) document.documentElement.style.setProperty("--hdr", `${Math.round(hdr.getBoundingClientRect().height)}px`); };
    set();
    let raf = 0;
    const ro = hdr ? new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(set); }) : null;
    if (hdr) ro?.observe(hdr);
    return () => { cancelAnimationFrame(raf); document.body.classList.remove("in-room"); document.documentElement.style.removeProperty("--hdr"); ro?.disconnect(); };
  }, [join]);
  const stageSources: StageSource[] = useMemo(() => split.stage.map((p) => ({ identity: p.identity, name: p.name, local: p.local, screen: p.screen, speaking: p.speaking })), [split]);
  const st = useMeetingStage({ meetingId, sources: stageSources, boardOpen, canViewBoard, mobile });
  const focusOn = boardFocus && st.boardItem && !ended;
  useEffect(() => { document.body.classList.toggle("board-focus", focusOn); return () => document.body.classList.remove("board-focus"); }, [focusOn]);
  const [shareBlocked, setShareBlocked] = useState<Set<string>>(() => new Set());
  const menu = useContextMenu();         // меню «Макет» и плитки доски (то же единое меню, что и у плиток участников)
  useEffect(() => { if (st.boardItem) setBoardMounted(true); }, [st.boardItem]);     // ведущий показал доску всем — редактор нужен и тем, кто её не открывал

  const roomRef = useRef<LkRoom | null>(null);
  const audioBox = useRef<HTMLDivElement>(null);
  const meetingRef = useRef<string | null>(null);
  const infoRef = useRef<JoinInfo | null>(null);
  const timeline = useRef(new JoinTimeline());
  const meter = useRef(new RateMeter());
  const pwRef = useRef<string | undefined>(undefined);
  const userStopRef = useRef(false);
  const screenIntendedRef = useRef(false);
  const screenStoppedOnceRef = useRef(false);
  const micWantedRef = useRef(false);
  const leavingRef = useRef(false);
  const endedRef = useRef(false);
  const rejoinTimer = useRef<number | undefined>(undefined);
  const joinReportedRef = useRef(false);
  const instanceRef = useRef("");
  const prepMicRef = useRef<Promise<LocalAudioTrack | Error> | null>(null);
  const freezeRef = useRef(new FreezeDetector(3));
  const asrWasReadyRef = useRef(false);
  const signalReachedRef = useRef(false);   // в текущей попытке сигнальное соединение (WebSocket) установилось — значит, дальше этап ICE

  const dlog = useCallback((msg: string) => {
    setLog((l) => [...l.slice(-79), `${new Date().toLocaleTimeString("ru-RU")}  ${redactSecrets(msg, 300)}`]);
  }, []);
  const setErr = useCallback((k: CtlKey, msg?: string) => setCtlErr((e) => ({ ...e, [k]: msg })), []);
  /** Жизненный цикл Room: каждая фаза — в журнал комнаты и на сервер с идентификатором объекта (видно, создан ли НОВЫЙ Room). */
  const phase = useCallback((ph: RoomPhase, extra = "") => {
    reportRoomPhase(ph, instanceRef.current, meetingRef.current ?? undefined, extra);
    dlog(`[${ph}] instance=${instanceRef.current}${extra ? ` ${extra}` : ""}`);
  }, [dlog]);
  const screenPhase = useCallback((ph: ScreenPhase, extra = "") => {
    reportScreenPhase(ph, instanceRef.current, meetingRef.current ?? undefined, extra);
    dlog(`[${ph}]${extra ? ` ${extra}` : ""}`);
  }, [dlog]);

  const refreshNow = useCallback(() => {
    const r = roomRef.current;
    if (!r) return;
    const all: Participant[] = [r.localParticipant, ...Array.from(r.remoteParticipants.values())];
    const next: PView[] = all.map((p) => ({
      identity: p.identity, name: tileName(p.identity, p.name, p.attributes), local: p.isLocal, mic: p.isMicrophoneEnabled, cam: p.isCameraEnabled,
      screen: p.isScreenShareEnabled, speaking: p.isSpeaking, participant: p,
    }));
    setParticipants((prev) => (sameList(prev, next, PVIEW_KEYS) ? prev : next));        // ничего не изменилось — состояние прежнее, перерисовки нет
  }, []);
  const refreshTimer = useRef<number | undefined>(undefined);
  const refresh = useCallback(() => {
    if (refreshTimer.current !== undefined) return;
    refreshTimer.current = window.setTimeout(() => { refreshTimer.current = undefined; refreshNow(); }, REFRESH_MS);
  }, [refreshNow]);

  /** Ошибка действия с устройством: конкретная причина у кнопки + запись в журнал сервера. */
  const fail = useCallback((action: MediaAction, key: CtlKey, event: string, e: unknown) => {
    const info = describeMediaError(e, action);
    if (!info.benign) setErr(key, info.message);
    dlog(`${action}: ${info.reason}${info.benign ? " (отмена пользователем)" : ""} — ${String((e as Error)?.message ?? e).slice(0, 140)}`);
    if (!info.benign) reportEvent(event, { meetingId: meetingRef.current ?? undefined, reason: info.reason, detail: String((e as Error)?.message ?? e) });
    return info;
  }, [dlog, setErr]);

  const stopScreenBookkeeping = useCallback((reason: ScreenStopReason) => {
    if (!screenIntendedRef.current && reason !== "user_button") return;
    screenIntendedRef.current = false;
    screenStoppedOnceRef.current = true;
    userStopRef.current = false;
    const mid = meetingRef.current ?? undefined;
    reportEvent("screen_track_unpublished", { meetingId: mid, reason });
    if (reason === "browser_stop") reportEvent("screen_share_ended_by_browser", { meetingId: mid, reason });
    else reportEvent("screen_share_stopped", { meetingId: mid, reason });
    dlog(`экран остановлен: ${reason}`);
    if (reason !== "user_button") setErr("screen", SCREEN_STOP_TEXT[reason]);
  }, [dlog, setErr]);

  const teardown = useCallback(async (notify: boolean) => {
    leavingRef.current = true;
    window.clearTimeout(rejoinTimer.current);
    window.clearTimeout(refreshTimer.current); refreshTimer.current = undefined;
    window.clearTimeout(floorTimer.current); floorTimer.current = undefined;
    const r = roomRef.current;
    roomRef.current = null;
    const prep = prepMicRef.current;
    prepMicRef.current = null;
    void prep?.then((t) => { if (!(t instanceof Error)) t.stop(); });
    if (r) reportRoomPhase("ROOM_DISPOSE", instanceRef.current, meetingRef.current ?? undefined);
    await r?.disconnect().catch(() => undefined);
    audioBox.current?.replaceChildren();
    if (notify && meetingRef.current) await (guestRef.current ? api.guest.leave() : api.leave(meetingRef.current)).catch(() => undefined);
    meetingRef.current = null;
    setActiveMeeting(null);
    setParticipants([]);
  }, []);

  /** Данные для входа: сотрудник — запрос /join; гость — уже выданные при входе по ссылке, а при повторном входе — новый токен по сессии гостя. */
  const fetchJoin = useCallback(async (pw?: string): Promise<JoinInfo> => {
    const g = guestRef.current;
    if (g) {
      if (firstJoinRef.current) { firstJoinRef.current = false; return g.info; }
      return api.guest.rejoin();
    }
    return api.join(roomId, pw);
  }, [roomId]);

  useEffect(() => { leavingRef.current = false; return () => { void teardown(true); }; }, [teardown]);

  // Draw.io прогревается в фоне, когда звонок уже установлен (можно говорить): тяжёлый редактор не задерживает вход, а открытие доски потом мгновенное.
  // Пропускается на слабых устройствах и при экономии трафика.
  useEffect(() => {
    if (stage !== "ready" || boardMounted || !canViewBoard || ended) return;
    const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } };
    if ((nav.deviceMemory ?? 8) <= 2 || nav.connection?.saveData) return;
    const run = () => setBoardMounted(true);
    const ric = (window as unknown as { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
    const h = ric ? ric(run, { timeout: 4000 }) : window.setTimeout(run, 2500);
    return () => { if (ric) (window as unknown as { cancelIdleCallback?: (n: number) => void }).cancelIdleCallback?.(h as number); else window.clearTimeout(h); };
  }, [stage, boardMounted, canViewBoard, ended]);
  useEffect(() => {
    const onHide = () => { if (meetingRef.current) leaveOnUnload(meetingRef.current); };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, []);

  // секундомер этапов: обновляем экран, пока вход не завершён
  useEffect(() => {
    if (stage === "ready" && !busy) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 500);
    return () => window.clearInterval(t);
  }, [stage, busy]);

  // ASR не входит в критический путь звонка: следим за его готовностью и показываем «запускается»/«временно недоступна»
  useEffect(() => {
    if (!join || !join.room.transcription_enabled) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetch("/api/v1/health/ready", { credentials: "same-origin" });
        const j = await r.json().catch(() => null);
        if (cancelled) return;
        const ok = !!j?.checks?.asr?.ok;
        if (ok) asrWasReadyRef.current = true;
        setAsrReady(ok);
        setAsrLost(!ok && asrWasReadyRef.current);
      } catch { /* повторим */ }
    };
    const t = window.setInterval(poll, asrReady ? 15000 : 4000);
    return () => { cancelled = true; window.clearInterval(t); };
  }, [join, asrReady]);

  // ------------------------------------------------------------------------------ LiveKit
  const sendJoinReport = useCallback(() => {
    if (joinReportedRef.current || !meetingRef.current) return;
    joinReportedRef.current = true;
    api.clientMetrics(metricsBody(meetingRef.current, null, timeline.current.metrics()));
    const m = timeline.current.metrics();
    const lkUrl = infoRef.current?.livekit_url ?? "";
    // реальные параметры соединения (без токенов): адрес, время сигнала и ICE, выбранный транспорт, состояние ICE, пара кандидатов
    window.setTimeout(() => {
      const room = roomRef.current;
      if (!room) return;
      void describeConnection(room).then((c) => reportEvent("livekit_connection", { meetingId: meetingRef.current ?? undefined, room: infoRef.current?.room.name,
        reason: c.transport ? `${c.transport} ${c.ice_state ?? ""}`.trim() : undefined, data: { livekit_url: safeUrl(lkUrl), signal_ms: m.signaling_connect_ms ?? null, ice_ms: m.ice_connect_ms ?? null, ...c } }));
    }, 1500);
    reportEvent("join_ok", { meetingId: meetingRef.current, room: infoRef.current?.room.name, detail: JSON.stringify(m) });
    // Долгая установка медиасоединения у отдельного клиента — частая жалоба: сразу записываем сеть и оборудование этого клиента
    const slow = (m.ice_connect_ms ?? 0) > 4000 || (m.total_join_ms ?? 0) > 8000;
    void collectAll().then((data) => {
      reportEvent(slow ? "ice_slow" : "network_info", { meetingId: meetingRef.current ?? undefined, room: infoRef.current?.room.name,
        reason: slow ? `ice=${m.ice_connect_ms ?? "?"}мс всего=${m.total_join_ms ?? "?"}мс` : undefined, data: { ...data, ice_connect_ms: m.ice_connect_ms ?? null, signaling_ms: m.signaling_connect_ms ?? null, total_join_ms: m.total_join_ms ?? null } });
    });
  }, []);

  const enableMic = useCallback(async () => {
    const lp = roomRef.current?.localParticipant;
    if (!lp) return;
    const tl = timeline.current;
    tl.mark("micStart");
    try {
      const prep = prepMicRef.current;
      prepMicRef.current = null;
      if (prep) {
        const track = await prep; // getUserMedia начался сразу после /join и шёл параллельно с подключением
        if (track instanceof Error) throw track;
        await lp.publishTrack(track, { source: Track.Source.Microphone, name: "microphone" });
      } else {
        await lp.setMicrophoneEnabled(true, captureOptions(loadMicPrefs()));
      }
      tl.mark("micPublished");
      micWantedRef.current = true;
      setErr("mic", undefined);
      setMicFail(null);
      refresh();
    } catch (e) {
      const info = fail("mic", "mic", "mic_failed", e);
      const kind = isDeviceBusyError(e) ? "busy" : info.reason === "NotAllowedError" || info.reason === "PermissionDeniedError" ? "denied" : "other";
      setMicFail(kind);
      setErr("mic", `${info.message} Вы остаётесь в комнате без микрофона — его можно включить позже кнопкой «Микрофон».`);
      // сведения для разбора: сколько микрофонов видит браузер, их названия и состояние разрешений
      void collectAll().then((data) => reportEvent(kind === "busy" ? "mic_busy" : kind === "denied" ? "mic_permission_denied" : "mic_failed", {
        meetingId: meetingRef.current ?? undefined, room: infoRef.current?.room.name, reason: info.reason, detail: String((e as Error)?.message ?? e), data }));
      reportEvent("join_without_mic", { meetingId: meetingRef.current ?? undefined, room: infoRef.current?.room.name, reason: kind });
    }
    sendJoinReport();
  }, [fail, refresh, sendJoinReport, setErr]);

  const connectLivekit = useCallback(async (info: JoinInfo, kind: "initial" | "rejoin" | "retry") => {
    const tl = timeline.current;
    setStage("server");
    signalReachedRef.current = false;
    const room = new LkRoom(buildRoomOptions());
    instanceRef.current = newInstanceId();
    setInstance(instanceRef.current);
    setRoomsCreated((n) => n + 1);
    roomRef.current = room;
    tl.mark("roomCreated");
    phase("ROOM_CREATE", `reason=${kind}`);
    if (kind === "initial") tl.mark("connectStart");
    phase("CONNECT_START");
    let connectedOnce = false; // отказ ПЕРВОГО подключения обрабатывает вызывающий код; автоматический повторный вход — только после успешного
    room
      .on(RoomEvent.ParticipantConnected, refresh).on(RoomEvent.ParticipantDisconnected, refresh)
      .on(RoomEvent.TrackMuted, (pub, who) => {
        refresh();
        // микрофон выключили не мы сами (кнопкой) — значит, руководитель комнаты
        if (who.isLocal && pub.source === Track.Source.Microphone && Date.now() - lastMicToggle.current > 2500) {
          setNotice({ kind: "warn", text: "Руководитель выключил ваш микрофон. Когда будете готовы говорить, нажмите «Микрофон»." });
          reportEvent("muted_by_moderator", { meetingId: meetingRef.current ?? undefined, room: infoRef.current?.room.name });
        }
      }).on(RoomEvent.TrackUnmuted, refresh)
      .on(RoomEvent.TrackPublished, refresh).on(RoomEvent.TrackUnpublished, refresh)
      .on(RoomEvent.LocalTrackPublished, (pub) => {
        refresh();
        if (pub.source === Track.Source.Microphone) { tl.mark("micPublished"); dlog("микрофон опубликован"); }
        if (pub.source === Track.Source.ScreenShare) {
          screenIntendedRef.current = true;
          reportEvent("screen_track_published", { meetingId: meetingRef.current ?? undefined });
          screenPhase("SCREEN_PUBLISH_OK");
          const ms = pub.track?.mediaStreamTrack;
          const st = ms?.getSettings?.();
          dlog(`экран опубликован ${st?.width ?? "?"}×${st?.height ?? "?"}@${Math.round(st?.frameRate ?? 0) || "?"} звук=${pub.track && room.localParticipant.getTrackPublication(Track.Source.ScreenShareAudio) ? "да" : "нет"}`);
          ms?.addEventListener("ended", () => {
            reportEvent("screen_track_ended", { meetingId: meetingRef.current ?? undefined, reason: userStopRef.current ? "user_button" : "browser_or_source_closed" });
            screenPhase("SCREEN_TRACK_ENDED", userStopRef.current ? "reason=user_button" : "reason=browser_or_source_closed");
            dlog("трек экрана завершён (ended)");
          }, { once: true });
        }
      })
      .on(RoomEvent.LocalTrackUnpublished, (pub) => {
        refresh();
        if (pub.source !== Track.Source.ScreenShare) return;
        screenPhase("SCREEN_UNPUBLISH");
        const connected = roomRef.current?.state === ConnectionState.Connected;
        stopScreenBookkeeping(userStopRef.current ? "user_button" : connected ? "browser_stop" : "connection_lost");
      })
      .on(RoomEvent.TrackSubscribed, (track, pub, participant) => {
        if (track.kind === Track.Kind.Audio && audioBox.current) {
          const el = track.attach();
          el.dataset.identity = participant.identity;
          const identity = participant.identity;
          const reapply = () => { el.muted = localMutedRef.current.has(identity); };      // заглушённый для себя остаётся заглушённым и после переподключения/перезагрузки
          reapply();
          el.addEventListener("loadedmetadata", reapply); el.addEventListener("playing", reapply);          // LiveKit при старте воспроизведения сам сбрасывает muted
          audioBox.current.appendChild(el);
          window.setTimeout(reapply, 500);
        }
        if (pub.source === Track.Source.ScreenShare) dlog("получен экран участника");
        refresh();
      })
      .on(RoomEvent.TrackUnsubscribed, (track, pub) => {
        track.detach().forEach((el) => el.remove());
        if (pub.source === Track.Source.ScreenShare) dlog("экран участника больше не приходит");
        refresh();
      })
      .on(RoomEvent.ActiveSpeakersChanged, refresh)
      .on(RoomEvent.AudioPlaybackStatusChanged, () => {
        const blocked = !room.canPlaybackAudio;
        setAudioBlocked(blocked);
        if (blocked) reportEvent("autoplay_blocked", { meetingId: meetingRef.current ?? undefined });
      })
      .on(RoomEvent.MediaDevicesError, (e) => { fail("device", "device", "device_error", e); })
      .on(RoomEvent.SignalConnected, () => { signalReachedRef.current = true; tl.mark("signalConnected"); setStage("media"); phase("SIGNALING_CONNECTED"); })
      .on(RoomEvent.ConnectionStateChanged, setState)
      .on(RoomEvent.Reconnecting, () => { phase("RECONNECTING"); reportEvent("reconnecting", { meetingId: meetingRef.current ?? undefined }); })
      .on(RoomEvent.Reconnected, () => {
        phase("RECONNECTED");
        reportEvent("reconnected", { meetingId: meetingRef.current ?? undefined });
        if (screenIntendedRef.current && !room.localParticipant.isScreenShareEnabled) stopScreenBookkeeping("connection_lost");
        refresh();
      })
      .on(RoomEvent.Disconnected, (reason) => {
        setState(ConnectionState.Disconnected);
        phase("DISCONNECTED", `reason=${reason ?? "unknown"}`);
        reportEvent("disconnected", { meetingId: meetingRef.current ?? undefined, reason: String(reason ?? "") });
        if (roomRef.current !== room || !connectedOnce) return; // старая комната после повторного входа / подключение ещё не состоялось
        if (screenIntendedRef.current) stopScreenBookkeeping("connection_lost");
        if (leavingRef.current || endedRef.current || reason === DisconnectReason.CLIENT_INITIATED) return;
        if (reason === DisconnectReason.DUPLICATE_IDENTITY) { setErr("general", describeMediaError(new Error("DUPLICATE_IDENTITY"), "connect").message); return; }
        if (reason === DisconnectReason.PARTICIPANT_REMOVED && Date.now() - floorRevokedAt.current < 30000) { scheduleRejoin(0); return; }       // слово забрали, а мы не успели снять дорожки: заходим зрителем
        if (reason === DisconnectReason.PARTICIPANT_REMOVED || reason === DisconnectReason.ROOM_DELETED) {
          setErr("general", "Вас отключили от комнаты: встреча закрыта или вас удалил руководитель.");
          return;
        }
        scheduleRejoin(0);
      });
    await room.connect(info.livekit_url, info.token);
    connectedOnce = true;
    tl.mark("mediaConnected");
    phase("ICE_CONNECTED");
    tl.mark("active");
    tl.finish();
    setStage("ready");
    phase("CONNECT_OK", `total=${tl.metrics().total_join_ms ?? "?"}ms`);
    // startAudio не должен задерживать публикацию микрофона и показ комнаты
    void room.startAudio().catch(() => undefined).then(() => setAudioBlocked(!room.canPlaybackAudio));
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dlog, fail, phase, refresh, screenPhase, setErr, stopScreenBookkeeping]);

  /** Права на публикацию и доску приходят вместе с токеном; после повторного входа применяются заново (слово сохраняется на сервере). */
  const applyClient = useCallback((info: JoinInfo) => {
    setSources(info.client.sources ?? ALL_SOURCES);
    setCanBoard(info.client.can_edit_board ?? true);
    setCanViewBoard(info.client.can_view_board ?? true);
    setTranscribing(info.transcription ?? true);
    setRecording(info.recording);
  }, []);

  const loadFloor = useCallback((mid: string) => {
    api.floor(mid).then((f) => { setFloorIds(new Set(f.floor)); setLeaderIds(new Set(f.leaders)); }).catch(() => undefined);
    api.hands(mid).then((r) => setHands(r.hands)).catch(() => undefined);
  }, []);
  /** Приход руководителя меняет список руководителей: перечитываем его не сразу и не чаще раза в несколько секунд (со случайной паузой, чтобы клиенты не били в сервер одновременно). */
  const floorTimer = useRef<number | undefined>(undefined);
  const floorSoon = useCallback((mid: string) => {
    if (floorTimer.current !== undefined) return;
    floorTimer.current = window.setTimeout(() => {
      floorTimer.current = undefined;
      if (meetingRef.current === mid) api.floor(mid).then((f) => { setFloorIds(new Set(f.floor)); setLeaderIds(new Set(f.leaders)); }).catch(() => undefined);
    }, 1500 + Math.random() * 3500);
  }, []);

  const scheduleRejoin = useCallback((attempt: number) => {
    window.clearTimeout(rejoinTimer.current);
    if (attempt >= MAX_REJOIN) {
      setRejoin(null);
      setErr("general", "Не удалось восстановить соединение с комнатой. Проверьте сеть и нажмите «Войти снова».");
      reportEvent("rejoin_failed", { meetingId: meetingRef.current ?? undefined, reason: "attempts_exhausted" });
      return;
    }
    setRejoin({ attempt: attempt + 1 });
    const delay = backoffDelay(attempt);
    dlog(`повторный вход: попытка ${attempt + 1} через ${Math.round(delay / 100) / 10} с`);
    rejoinTimer.current = window.setTimeout(async () => {
      if (leavingRef.current || endedRef.current) return;
      reportEvent("rejoin_started", { meetingId: meetingRef.current ?? undefined, reason: String(attempt + 1) });
      try {
        await roomRef.current?.disconnect().catch(() => undefined);
        const info = await fetchJoin(pwRef.current); // свежий токен: прежний мог устареть
        infoRef.current = info;
        applyClient(info);
        await connectLivekit(info, "rejoin");
        if (micWantedRef.current) void enableMic();
        setRejoin(null);
        setErr("general", undefined);
        dlog("повторный вход выполнен");
      } catch (e) {
        const ae = e as ApiError;
        if (ae instanceof ApiError && ae.status >= 400 && ae.status < 500 && ae.status !== 429) {
          setRejoin(null);
          setErr("general", `Повторный вход невозможен: ${ae.message}`);
          reportEvent("rejoin_failed", { meetingId: meetingRef.current ?? undefined, reason: ae.code, detail: ae.message });
          return;
        }
        scheduleRejoin(attempt + 1);
      }
    }, delay);
  }, [applyClient, connectLivekit, dlog, enableMic, fetchJoin, setErr]);

  const connect = async (pw?: string) => {
    setBusy(true);
    setError("");
    setCtlErr({});
    const tl = timeline.current;
    tl.reset(); joinReportedRef.current = false; leavingRef.current = false; endedRef.current = false;
    tl.mark("click"); tl.mark("joinStart");
    setStage("prepare");
    let info: JoinInfo;
    try {
      info = await fetchJoin(pw);
    } catch (e) {
      const ae = e as ApiError;
      if (ae.code === "room_password_required" || ae.code === "room_password_invalid") {
        setNeedPassword(true);
        setError(ae.code === "room_password_invalid" ? ae.message : "");
      } else {
        setError(e instanceof ApiError ? ae.message || "Не удалось войти в комнату" : describeMediaError(e, "connect").message);
        reportEvent("join_failed", { reason: ae.code ?? "error", detail: ae.message });
      }
      setBusy(false);
      return;
    }
    tl.mark("joinEnd");
    pwRef.current = pw;
    infoRef.current = info;
    meetingRef.current = info.meeting_id;
    applyClient(info);
    setFloorIds(new Set()); setLeaderIds(new Set()); setHands([]);
    loadFloor(info.meeting_id);
    setAsrReady(info.asr_ready);
    asrWasReadyRef.current = info.asr_ready;
    setAsrLost(false);
    setWithAudio(info.client.screen_share_audio);
    setNeedPassword(false);
    setWelcome(info.client.welcome_message ?? null);
    setNotice(info.client.presentation && !(info.client.sources ?? []).length
      ? { kind: "info", text: "Это презентационная комната: вы слушаете. Микрофон, камера и показ экрана появятся, когда руководитель даст вам слово." }
      : info.client.mute_on_join ? { kind: "info", text: "В этой переговорке микрофон по умолчанию выключен. Чтобы говорить, нажмите «Микрофон»." } : null);
    setJoin(info); // комната и канал событий открываются сразу — параллельно с подключением к LiveKit
    setActiveMeeting(info.meeting_id); // верхняя панель с этого момента открывает разделы в новых вкладках
    const pre = takePreJoin(); // устройства, выбранные на проверке оборудования (гость)
    setBusy(false);
    // Критический путь — Room.connect. Всё независимое идёт рядом: микрофон (getUserMedia, включая запрос разрешения) начинается
    // немедленно и не ждёт подключения; канал событий (WebSocket) открывает TranscriptPanel при появлении комнаты.
    tl.mark("gumStart");
    // «микрофон по умолчанию выключен» (настройка переговорки): звук не запрашиваем и не включаем — пользователь включит сам
    const mayPublishMic = (info.client.sources ?? ALL_SOURCES).includes("microphone");
    if (!info.client.mute_on_join && mayPublishMic) prepMicRef.current = createLocalAudioTrack({ ...captureOptions(loadMicPrefs()), ...(pre?.micId ? { deviceId: pre.micId } : {}) }).then((t) => { tl.mark("gumEnd"); return t; }, (e: unknown) => { tl.mark("gumEnd"); return e instanceof Error ? e : new Error(String(e)); });
    void collectAll().then((data) => reportEvent("join_attempt", { meetingId: info.meeting_id, room: info.room.name, data }));
    try {
      // Первое подключение переживает кратковременные сбои сети/ICE: до MAX_CONNECT_TRIES попыток с нарастающей паузой и свежим токеном
      for (let attempt = 1; ; attempt++) {
        try {
          await connectLivekit(info, attempt === 1 ? "initial" : "retry");
          break;
        } catch (e) {
          const stage: FailStage = failStage(e, signalReachedRef.current ? "media" : "server");
          const m = describeMediaError(e, "connect", { stage: signalReachedRef.current ? "media" : "server" });
          if (attempt >= MAX_CONNECT_TRIES || !isTransientConnectError(e) || leavingRef.current) throw e;
          reportEvent("connect_retry", { meetingId: info.meeting_id, room: info.room.name, reason: m.reason, detail: String((e as Error)?.message ?? e), data: { attempt, fail_stage: stage, livekit_url: safeUrl(info.livekit_url) } });
          dlog(`подключение не удалось на этапе «${stage === "ice" ? "ICE" : "сигнал/WebSocket"}» (${m.reason}), попытка ${attempt + 1} из ${MAX_CONNECT_TRIES}`);
          setConnectTry({ attempt: attempt + 1, max: MAX_CONNECT_TRIES });
          const failed = roomRef.current;
          roomRef.current = null;
          await failed?.disconnect().catch(() => undefined);
          await sleep(1200 * attempt);
          try { info = await fetchJoin(pw); infoRef.current = info; meetingRef.current = info.meeting_id; } catch { /* прежний токен ещё может подойти */ }
        }
      }
      setConnectTry(null);
      if (!info.client.mute_on_join && mayPublishMic) void enableMic();
      const lk = roomRef.current;
      if (lk && pre?.speakerId) void lk.switchActiveDevice("audiooutput", pre.speakerId).catch(() => undefined);
      if (lk && pre?.camOn && info.room.camera_allowed && (info.client.sources ?? ALL_SOURCES).includes("camera")) void lk.localParticipant.setCameraEnabled(true, pre.camId ? { deviceId: pre.camId } : undefined).then(refresh).catch((e) => fail("camera", "cam", "camera_failed", e));
    } catch (e) {
      const reached = signalReachedRef.current;
      const m = describeMediaError(e, "connect", { stage: reached ? "media" : "server" });
      const stage: FailStage = failStage(e, reached ? "media" : "server");
      setConnectTry(null);
      reportEvent("join_failed", { meetingId: info.meeting_id, room: info.room.name, reason: m.reason, detail: String((e as Error)?.message ?? e) });
      // проверяем сигнальный сервер отдельным HTTPS-запросом: отличает «сеть режет соединение» от «сервер звонков не запущен/прокси сломан»
      const probe: SignalProbe | null = stage === "signal" ? await probeSignal(info.livekit_url) : null;
      const tm = timeline.current.metrics();
      void collectAll().then((data) => reportEvent(m.reason === "IceFailed" ? "ice_failed" : "connect_failed", {
        meetingId: info.meeting_id, room: info.room.name, reason: m.reason, detail: String((e as Error)?.message ?? e),
        // свои поля — первыми: сервер принимает не более 24 ключей, а данные клиента многочисленны
        data: { fail_stage: stage, livekit_url: safeUrl(info.livekit_url), signal_ms: tm.signaling_connect_ms ?? null, attempts: MAX_CONNECT_TRIES,
          probe_ok: probe?.ok ?? null, probe_status: probe?.status ?? null, probe_ms: probe?.ms ?? null, probe_error: probe?.error ?? null, ...data } }));
      await teardown(true);
      setJoin(null);
      setError(probe ? `${m.message} Проверка: ${describeProbe(probe)}.` : m.message);
    }
  };

  // Гость уже прошёл проверку оборудования — входим сразу. Отложенный запуск переживает двойной вызов эффектов (StrictMode).
  useEffect(() => {
    if (!guest) return;
    const t = window.setTimeout(() => { void connect(); }, 0);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guest]);

  // Закрытие/перезагрузка вкладки во время встречи — только после подтверждения: соединение с комнатой оборвётся.
  useEffect(() => {
    if (!join || ended) return;
    const guard = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [join, ended]);

  // ------------------------------------------------------------------- статистика (раз в 5 с)
  useEffect(() => {
    if (!join || stage !== "ready") return;
    let n = 0;
    const t = window.setInterval(async () => {
      const room = roomRef.current;
      if (!room || document.visibilityState !== "visible") return;
      // Зритель презентации ничего не публикует: статистику (getStats по всем соединениям) собирает лишь каждый двадцатый — для диагностики хватает,
      // а тысячи зрителей не шлют серверу тысячи отчётов.
      const lp = room.localParticipant;
      if (join.client.presentation && !join.client.can_manage && !(lp.isMicrophoneEnabled || lp.isCameraEnabled || lp.isScreenShareEnabled) && statBucket(join.identity) !== 0) return;
      const snap = await sampleRoom(room, meter.current, freezeRef.current);
      setSnapshot(snap);
      if (snap.screenFrozen) { dlog("показ экрана участника «завис»: framesDecoded не растёт"); reportEvent("screen_frozen", { meetingId: meetingRef.current ?? undefined, reason: "framesDecoded_stalled" }); }
      if (++n % 6 === 0 && meetingRef.current) api.clientMetrics(metricsBody(meetingRef.current, snap));
    }, 5000);
    return () => window.clearInterval(t);
  }, [join, stage]);

  // ------------------------------------------------------------------------------- действия
  const someoneElseSharing = participants.some((p) => p.screen && !p.local);

  const toggle = async (what: "mic" | "cam" | "screen") => {
    const lp = roomRef.current?.localParticipant;
    if (!lp || !join) return;
    if (what === "mic") { lastMicToggle.current = Date.now(); setNotice(null); }
    setErr(what === "cam" ? "cam" : what, undefined);
    try {
      if (what === "mic") {
        await lp.setMicrophoneEnabled(!lp.isMicrophoneEnabled);
        micWantedRef.current = lp.isMicrophoneEnabled;
      }
      if (what === "cam") await lp.setCameraEnabled(!lp.isCameraEnabled);
      if (what === "screen") {
        if (lp.isScreenShareEnabled) {
          userStopRef.current = true;
          await lp.setScreenShareEnabled(false);
          stopScreenBookkeeping("user_button");
        } else {
          if (join.client.one_sharer_at_a_time && someoneElseSharing) { setErr("screen", "Сейчас экран уже показывает другой участник."); return; }
          const profile = loadProfile(join.client.screen_profile);
          const o = screenShareOptions(profile, join.client.screen_share_audio && withAudio, join.client.screen_eco_kbps);
          userStopRef.current = false;
          screenPhase("SCREEN_CREATE", `profile=${profile} audio=${o.capture.audio ? "yes" : "no"}`);
          screenPhase("SCREEN_PUBLISH_START");
          await lp.setScreenShareEnabled(true, o.capture, o.publish);
          reportEvent(screenStoppedOnceRef.current ? "screen_share_restarted" : "screen_share_started", { meetingId: join.meeting_id, detail: `${profile}, audio=${o.capture.audio}` });
          if (profile === "eco") void enforceEco(join.client.screen_eco_kbps);
        }
      }
    } catch (e) {
      if (what === "screen") { screenIntendedRef.current = false; screenPhase("SCREEN_ERROR", `reason=${(e as Error)?.name ?? "error"}`); fail("screen", "screen", "screen_share_failed", e); }
      else if (what === "mic") fail("mic", "mic", "mic_failed", e);
      else fail("camera", "cam", "camera_failed", e);
    }
    refresh();
  };

  /** «Экономный 720p»: браузер мог захватить экран крупнее заказанного — прижимаем исходящий поток к 1280×720/10 к/с/потолку битрейта и честно сообщаем, что получилось (по getStats). */
  const enforceEco = useCallback(async (kbps?: number) => {
    const lp = roomRef.current?.localParticipant;
    const track = lp?.getTrackPublication(Track.Source.ScreenShare)?.track as LocalVideoTrack | undefined;
    if (!track) return;
    await applyProfileLive(track, "eco", kbps);
    await sleep(4000);
    const live = roomRef.current?.localParticipant.getTrackPublication(Track.Source.ScreenShare)?.track as LocalVideoTrack | undefined;
    if (!live) return;
    const stat = await measureScreen(live);
    const rep = ecoReport(stat, kbps);
    setNotice({ kind: rep.ok ? "info" : "warn", text: rep.text });
    reportEvent("screen_eco_measured", { meetingId: meetingRef.current ?? undefined, reason: rep.ok ? "ok" : "off_target",
      data: { width: stat?.frameWidth ?? null, height: stat?.frameHeight ?? null, fps: Math.round(stat?.framesPerSecond ?? 0), target_kbps: stat?.targetBitrate ? Math.round(stat.targetBitrate / 1000) : null, limit: stat?.qualityLimitationReason ?? null } });
  }, []);
  /** Выбор профиля показа экрана участником: запоминается; идущий показ не прерывается — применяется то, что браузер позволяет менять на лету. */
  const [profilePick, setProfilePick] = useState<ScreenProfile | null>(null);
  const chooseProfile = useCallback(async (pr: ScreenProfile) => {
    saveProfile(pr);
    setProfilePick(pr);
    const lp = roomRef.current?.localParticipant;
    const track = lp?.getTrackPublication(Track.Source.ScreenShare)?.track as LocalVideoTrack | undefined;
    const kbps = infoRef.current?.client.screen_eco_kbps;
    if (!track) { setNotice({ kind: "info", text: `Профиль показа экрана: «${PROFILE_LABELS[pr]}». Он применится при следующем показе.` }); return; }
    const r = await applyProfileLive(track, pr, kbps);
    setNotice({ kind: r === "none" ? "warn" : "info", text: r === "live" ? `Профиль «${PROFILE_LABELS[pr]}» применён к идущему показу без остановки.`
      : r === "partial" ? `Профиль «${PROFILE_LABELS[pr]}»: разрешение, частота и битрейт применены сразу; кодек и число слоёв изменятся при следующем показе.`
      : "Браузер не позволил изменить параметры идущего показа: профиль применится при следующем показе." });
    if (pr === "eco") void enforceEco(kbps);
  }, [enforceEco]);

  /** Применяет настройки микрофона: сохраняет в браузере и «на лету» перезапускает захват (без повторного входа в комнату). */
  const applyMicPrefs = useCallback(async (next: MicPrefs, changed: string) => {
    setMicPrefs(next);
    saveMicPrefs(next);
    reportEvent("noise_suppression_changed", { meetingId: meetingRef.current ?? undefined, room: infoRef.current?.room.name, reason: changed, data: { ...next } });
    const pub = roomRef.current?.localParticipant.getTrackPublication(Track.Source.Microphone);
    const track = pub?.audioTrack as LocalAudioTrack | undefined;
    if (!track) return;
    try { await track.restartTrack(captureOptions(next)); setErr("mic", undefined); }
    catch (e) { fail("mic", "mic", "mic_failed", e); }
  }, [fail, setErr]);
  const toggleNoise = () => applyMicPrefs({ ...micPrefs, noiseSuppression: !micPrefs.noiseSuppression }, micPrefs.noiseSuppression ? "noise_off" : "noise_on");

  /** Руководитель: выключить микрофоны у всех или у одного участника. */
  const moderate = async (who?: PView) => {
    if (!join) return;
    try {
      const r = who ? await api.muteOne(join.meeting_id, who.identity) : await api.muteAll(join.meeting_id);
      setNotice({ kind: "ok", text: who ? (r.muted ? `Микрофон выключен: ${who.name}.` : `У участника ${who.name} микрофон уже выключен.`) : (r.muted ? `Микрофоны выключены у участников: ${r.muted}.` : "Ни у кого не было включённого микрофона.") });
    } catch (e) { setNotice({ kind: "warn", text: (e as ApiError).message || "Не удалось выключить микрофоны." }); }
  };

  const toggleRecording = async () => {
    if (!join) return;
    setErr("rec", undefined);
    try { setRecording((await api.setRecording(join.meeting_id, !recording)).enabled); }
    catch (e) { setErr("rec", (e as ApiError).message || "Не удалось переключить запись."); }
  };

  /** «Остановить / возобновить транскрибацию»: звонок и запись звука не затрагиваются. */
  const toggleTranscription = async () => {
    if (!join) return;
    setErr("tr", undefined);
    try { setTranscribing((await api.setTranscription(join.meeting_id, !transcribing)).enabled); }
    catch (e) { setErr("tr", (e as ApiError).message || "Не удалось переключить транскрибацию."); }
  };

  /** «Дать слово» / «Забрать слово». */
  const giveFloor = async (who: PView, granted: boolean) => {
    if (!join) return;
    try {
      await api.setFloor(join.meeting_id, who.identity, granted);
      setFloorIds((cur) => { const n = new Set(cur); if (granted) n.add(who.identity); else n.delete(who.identity); return n; });
      setNotice({ kind: "ok", text: granted ? `Слово дано: ${who.name}.` : `Слово забрано: ${who.name}.` });
    } catch (e) { setNotice({ kind: "warn", text: (e as ApiError).message || "Не удалось изменить право слова." }); }
  };
  const removeParticipant = async (who: PView) => {
    if (!join) return;
    try { await api.kick(join.meeting_id, who.identity); setNotice({ kind: "ok", text: `${who.name} удалён из встречи.` }); }
    catch (e) { setNotice({ kind: "warn", text: (e as ApiError).message || "Не удалось удалить участника." }); }
  };
  /** Модерация показа экрана и камеры: сервер проверяет права и то, что участник — из этой встречи; остальные показы продолжаются. */
  const stopShare = async (who: PView, block: boolean) => {
    if (!join) return;
    try {
      const r = await api.stopShare(join.meeting_id, who.identity, block);
      if (block) setShareBlocked((cur) => new Set(cur).add(who.identity));
      setNotice({ kind: "ok", text: `${r.stopped ? `Показ экрана остановлен: ${who.name}.` : `${who.name} сейчас не показывает экран.`}${block ? " Повторный показ запрещён до конца встречи." : ""}` });
    } catch (e) { setNotice({ kind: "warn", text: (e as ApiError).message || "Не удалось остановить показ экрана." }); }
  };
  const allowShare = async (who: PView) => {
    if (!join) return;
    try {
      await api.allowShare(join.meeting_id, who.identity);
      setShareBlocked((cur) => { const n = new Set(cur); n.delete(who.identity); return n; });
      setNotice({ kind: "ok", text: `${who.name} снова может показывать экран.` });
    } catch (e) { setNotice({ kind: "warn", text: (e as ApiError).message || "Не удалось вернуть право показа." }); }
  };
  const stopCamera = async (who: PView) => {
    if (!join) return;
    try { const r = await api.stopCamera(join.meeting_id, who.identity); setNotice({ kind: "ok", text: r.stopped ? `Камера выключена: ${who.name}.` : `У участника ${who.name} камера уже выключена.` }); }
    catch (e) { setNotice({ kind: "warn", text: (e as ApiError).message || "Не удалось выключить камеру." }); }
  };

  /** Слово дали или забрали у НАС: права обновляются сразу (сервер уже изменил разрешения в звонке), забранное — выключаем. */
  const onMyFloor = useCallback((granted: boolean) => {
    const info = infoRef.current;
    if (!info || info.client.can_manage) return;
    if (granted) {
      floorRevokedAt.current = 0;
      setSources(ALL_SOURCES);          // слово даёт весь набор выступающего сразу: микрофон, камеру, показ экрана и доску (сервер выдал те же права в звонке)
      setCanBoard(info.client.board_access !== "leaders" && info.client.board_access !== "private");     // доска у руководителей «только им» — явный выбор руководителя
      setNotice({ kind: "ok", text: "Вам дали слово: можно включить микрофон, камеру, показ экрана и править доску. Микрофон и камера сами не включатся." });
    } else {
      floorRevokedAt.current = Date.now();
      setSources([]);
      setCanBoard(info.client.board_access === "everyone");
      const lp = roomRef.current?.localParticipant;
      if (lp) {   // новые публикации сервер уже запретил; активные выключаем сами, не дожидаясь отзыва
        userStopRef.current = true;
        void Promise.allSettled([lp.setMicrophoneEnabled(false), lp.setCameraEnabled(false), lp.setScreenShareEnabled(false)]).then(refresh);
      }
      micWantedRef.current = false;
      setNotice({ kind: "info", text: "Руководитель забрал слово: микрофон, камера и показ экрана выключены." });
    }
  }, [refresh]);

  const enableAudio = async () => {
    try { await roomRef.current?.startAudio(); setAudioBlocked(!(roomRef.current?.canPlaybackAudio ?? true)); setErr("audio", undefined); }
    catch (e) { fail("playback", "audio", "device_error", e); }
  };

  const onSocketStatus = useCallback((st: SocketStatus) => {
    setSocket(st);
    if (st.state === "connecting") timeline.current.mark("wsStart");
    if (st.state === "online") timeline.current.mark("wsOpen");
  }, []);
  const onLive = useCallback((e: LiveEvent) => {
    if (e.type === "recording_changed") setRecording(e.enabled);
    else if (e.type === "transcription_changed") setTranscribing(e.enabled);
    else if (e.type === "participant_joined") { if (meetingRef.current) floorSoon(meetingRef.current); }
    else if (e.type === "hand_changed") {
      // небольшая очередь приходит целиком; большая — одним изменением (сервер не рассылает сотни имён каждому зрителю)
      if (e.queue) setHands(e.queue);
      else setHands((cur) => {
        const rest = cur.filter((h) => h.identity !== e.identity);
        return e.raised ? [...rest, { identity: e.identity, name: e.name, at: e.at ?? Date.now() / 1000 }].sort((a, b) => a.at - b.at) : rest;
      });
      if (e.raised && e.identity !== infoRef.current?.identity) playHandSound();          // один тихий сигнал на событие; своя рука без звука
    }
    else if (e.type === "floor_changed") {
      setFloorIds((cur) => { const n = new Set(cur); if (e.granted) n.add(e.identity); else n.delete(e.identity); return n; });
      if (e.identity === infoRef.current?.identity) onMyFloor(e.granted);
    }
    else if (e.type === "stage_changed") st.applyServer(e);
    else if (e.type === "share_stopped" || e.type === "share_permission") {
      const blocked = e.type === "share_stopped" ? e.blocked : !e.allowed;
      if (e.type === "share_permission" || e.blocked) setShareBlocked((cur) => { const n = new Set(cur); if (blocked) n.add(e.identity); else n.delete(e.identity); return n; });
      if (e.identity !== infoRef.current?.identity) return;
      if (e.type === "share_stopped") {
        // сервер звонков уже выключил дорожку; прекращаем захват у себя, чтобы браузер перестал показывать «идёт трансляция»
        const lp = roomRef.current?.localParticipant;
        if (lp?.getTrackPublication(Track.Source.ScreenShare)) { userStopRef.current = true; void lp.setScreenShareEnabled(false).catch(() => undefined).then(() => { stopScreenBookkeeping("user_button"); refresh(); }); }
        if (e.blocked) setSources((s) => s.filter((x) => x !== "screen_share" && x !== "screen_share_audio"));
        setNotice({ kind: "warn", text: `${e.by || "Руководитель"} остановил ваш показ экрана.${e.blocked ? " Повторный показ в этой встрече запрещён — его может разрешить руководитель." : " Его можно начать снова."}` });
        reportEvent("screen_share_stopped", { meetingId: meetingRef.current ?? undefined, reason: e.blocked ? "moderator_block" : "moderator" });
      } else {
        const info = infoRef.current;
        if (info && info.room.screen_share_allowed && !guestRef.current) setSources((s) => (s.includes("microphone") || !info.client.presentation ? [...new Set([...s, "screen_share", "screen_share_audio"])] : s));
        setNotice({ kind: "ok", text: "Руководитель снова разрешил вам показывать экран." });
      }
    }
    else if (e.type === "camera_stopped" && e.identity === infoRef.current?.identity) {
      void roomRef.current?.localParticipant.setCameraEnabled(false).catch(() => undefined).then(refresh);
      setNotice({ kind: "warn", text: `${e.by || "Руководитель"} выключил вашу камеру. Её можно включить снова кнопкой «Камера».` });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [floorSoon, onMyFloor, st.applyServer, refresh, stopScreenBookkeeping]);

  // после переподключения канала событий состояние слова могло измениться — читаем заново
  useEffect(() => {
    if (!join) return;
    return bus.onResync(() => {
      const mid = meetingRef.current;
      if (!mid) return;
      loadFloor(mid);
      api.getStage(mid).then(st.applyServer).catch(() => undefined);       // общую сцену могли сменить, пока канал событий был разорван
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bus, join, loadFloor, st.applyServer]);
  const leave = async () => { await teardown(true); if (guest) guest.onLeft("left"); else navigate("/"); };
  const endForAll = async () => {
    if (meetingRef.current) await api.endMeeting(meetingRef.current).catch((e) => setErr("general", (e as ApiError).message));
    const mid = meetingRef.current;
    endedRef.current = true;
    await teardown(false);
    if (mid) navigate(`/history/${mid}`); else navigate("/");
  };
  const onMeetingEnded = useCallback(() => {
    endedRef.current = true;
    setEnded(true);
    if (!guestRef.current) setRedirectIn(6);
    void teardown(false);
  }, [teardown]);

  // после завершения — к странице встречи (там формируется протокол, пока участник на ней)
  useEffect(() => {
    if (redirectIn === null) return;
    if (redirectIn <= 0) { const mid = infoRef.current?.meeting_id; if (mid) navigate(`/history/${mid}`); return; }
    const t = window.setTimeout(() => setRedirectIn((n) => (n === null ? n : n - 1)), 1000);
    return () => window.clearTimeout(t);
  }, [redirectIn, navigate]);

  // --------------------------------------------------------------------- размер панели
  const persistTw = (w: number) => lsSet("room.tw", String(w));
  const onSplitDown = (e: ReactPointerEvent) => {
    e.preventDefault();
    const startX = e.clientX, startW = tw;
    let last = startW;
    const move = (ev: PointerEvent) => { last = clamp(startW + (startX - ev.clientX), TW_MIN, TW_MAX); setTw(last); };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); persistTw(last); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onSplitKey = (e: ReactKeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const w = clamp(tw + (e.key === "ArrowLeft" ? 24 : -24), TW_MIN, TW_MAX);
    setTw(w); persistTw(w);
  };
  const toggleCollapsed = () => setTCollapsed((c) => { lsSet("room.tcollapsed", c ? "0" : "1"); return !c; });

  // ---------------------------------------------------------------------- вид «до входа»
  const onHw = (p: PreJoinHw & { micOk: boolean }) => setPreJoin({ micId: p.micId, speakerId: p.speakerId, camId: p.camId, camOn: p.camOn });
  const tl = timeline.current;
  const done = { prepare: tl.metrics().join_api_ms, server: tl.metrics().signaling_connect_ms, media: tl.metrics().ice_connect_ms };
  if (!join && guest) {
    return (
      <section className="prejoin card">
        <h1>Подключаемся к комнате…</h1>
        {error && <div className="alert error" role="alert">{error}</div>}
        {error && <div className="row"><button className="btn primary" onClick={() => { firstJoinRef.current = true; void connect(); }}>Повторить</button>
          <button className="btn ghost" onClick={() => guest.onLeft("left")}>Выйти</button></div>}
        {busy && <ConnectProgress stage="prepare" elapsedMs={tl.stageMs("prepare")} done={done} />}
      </section>
    );
  }
  if (!join) {
    return (
      <PreJoin onHw={roomInfo?.room_type === "presentation" && !roomInfo.can_manage ? undefined : onHw} room={roomInfo} needPassword={needPassword || !!roomInfo?.has_password} password={password} onPassword={setPassword} error={error} busy={busy}
               onJoin={() => void connect(needPassword || roomInfo?.has_password ? password : undefined)} onBack={() => navigate("/")}
               progress={busy ? <ConnectProgress stage="prepare" elapsedMs={tl.stageMs("prepare")} done={done} /> : null} />
    );
  }

  const room = join.room;
  const isLeader = !!join.client.can_manage && !guest;
  const presentation = !!join.client.presentation;
  const canMic = sources.includes("microphone");
  const canCam = sources.includes("camera");
  const canScreen = sources.includes("screen_share") && !guest;
  const showCam = room.camera_allowed || canCam;
  const curProfile = profilePick ?? loadProfile(join.client.screen_profile);
  const showScreen = !guest && (room.screen_share_allowed || canScreen) && !!navigator.mediaDevices?.getDisplayMedia;       // на телефонах показа экрана из браузера нет — кнопка не занимает место
  const myFloor = floorIds.has(join.identity);
  const listenerHint = "В презентационной комнате вы слушаете. Когда руководитель даст слово, кнопка станет доступна";
  const handOrder = new Map(hands.map((h, i) => [h.identity, i + 1] as const));
  const viewParticipants: PView[] = split.stage.map((p) => ({ ...p, hand: handOrder.has(p.identity), handOrder: handOrder.get(p.identity) }));       // плитки — только у выступающих; зрители презентации — в списке
  const myHand = handOrder.has(join.identity);
  const toggleHand = () => { void api.hand(join.meeting_id, !myHand).then((r) => setHands(r.queue)).catch((e) => setErr("general", (e as ApiError).message)); };
  const lowerHand = (identity: string) => { void api.hand(join.meeting_id, false, identity).then((r) => setHands(r.queue)).catch((e) => setErr("general", (e as ApiError).message)); };
  const tileActions: TileActions | undefined = join.client.can_moderate || isLeader
    ? { presentation, onMute: moderate, onFloor: isLeader ? giveFloor : undefined, onKick: isLeader ? removeParticipant : undefined, onLowerHand: isLeader ? (v) => lowerHand(v.identity) : undefined,
        onStopCamera: (v) => void stopCamera(v), onStopShare: (v, block) => void stopShare(v, block), onAllowShare: (v) => void allowShare(v), shareBlocked } : undefined;
  const me = participants.find((p) => p.local);

  // ------------------------------------------------------------------ сцена: меню, отметки, общая сцена ведущего
  const pv = new Map(viewParticipants.map((p) => [p.identity, p] as const));
  const canSpot = !!join.client.can_moderate && !guest;
  const pinSet = new Set(st.personal.pins);
  const spotSet = new Set(st.spotIgnored ? [] : st.spot);
  const mainSet = new Set(st.choice.main);
  const titleOf = (k: string) => st.items.find((i) => i.key === k)?.title;
  const spotTitles = st.spot.map(titleOf).filter(Boolean) as string[];
  const cellOf = (k: string) => document.querySelector<HTMLElement>(`.st-cell[data-key="${CSS.escape(k)}"]`);
  const spotlight = (keys: string[]) => {
    void st.setSpot(keys).then(() => setNotice({ kind: "ok", text: keys.length ? `Показано всем: ${keys.map(titleOf).filter(Boolean).join(", ")}.` : "Общая сцена очищена: каждый видит свою раскладку." }))
      .catch((e) => setNotice({ kind: "warn", text: (e as ApiError).message || "Не удалось изменить общую сцену." }));
  };
  const onlyMine = (k: string) => st.choice.reason === "pins" && st.choice.main.length === 1 && mainSet.has(k);
  const openOrBack = (k: string) => (onlyMine(k) ? st.resetPins() : st.openLarge(k));
  const closeBoard = () => { setBoardFocus(false); setBoardOpen(false); st.unpin("board"); if (st.spot.includes("board") && !st.spotIgnored) st.ignoreSpot(); };
  /** Пункты сцены для любой плитки: локальные (видно только мне) — сверху, «для всех» (руководитель) — ниже. */
  const stageMenu = (it: StageItem): MenuItem[] => {
    const k = it.key, pinned = pinSet.has(k), spotted = st.spot.includes(k);
    return [
      { id: "large", label: onlyMine(k) ? "Вернуть общую раскладку" : "Открыть крупно", icon: "expand", onSelect: () => openOrBack(k) },
      { id: "pin", label: pinned ? "Открепить у себя" : "Закрепить у себя", icon: pinned ? "unpin" : "pin", hint: pinned ? "Вернуться к автоматическому выбору" : `Остаётся на вашей основной сцене; видно только вам; до ${MAX_PINS} элементов`, onSelect: () => st.togglePin(k) },
      { id: "fs", label: "Во весь экран", icon: "expand", hidden: it.type === "board", onSelect: () => toggleFullscreen(cellOf(k)?.firstElementChild) },
      { id: "pip", label: "Картинка в картинке", icon: "pip", hidden: it.type === "board" || !pipSupported(), onSelect: () => togglePip(cellOf(k)?.querySelector("video")) },
      { id: "spot", label: "Показать всем", hint: "Ведущий выводит это крупно у всех участников", icon: "spot", hidden: !canSpot || (spotted && st.spot.length === 1), onSelect: () => spotlight([k]) },
      { id: "spotadd", label: "Добавить на общую сцену", icon: "spot", hidden: !canSpot || spotted || !st.spot.length || st.spot.length >= MAX_PINS, onSelect: () => spotlight([...st.spot, k]) },
      { id: "spotdel", label: "Убрать с общей сцены", icon: "eyeOff", hidden: !canSpot || !spotted, onSelect: () => spotlight(st.spot.filter((x) => x !== k)) },
    ];
  };
  const boardItems = (): MenuItem[] => [...stageMenu({ key: "board", type: "board", title: "Доска", since: 0 }), { id: "closeboard", label: "Скрыть доску", icon: "close", onSelect: closeBoard }];
  const renderItem = (it: StageItem, s: { big: boolean }) => {
    if (it.type === "board") {
      return s.big ? null : (
        <div className="board-tile" role="button" tabIndex={0} aria-label="Доска. Enter — открыть крупно" onClick={() => st.openLarge("board")} onContextMenu={menu.onContextMenu(boardItems)}
             onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); st.openLarge("board"); } }}>
          <Icon name="board" size={28} /><span>Доска{boardNews ? " · обновлена" : ""}</span>
        </div>
      );
    }
    const p = pv.get(it.identity ?? "");
    if (!p) return null;
    if (it.type === "screen") {
      const own: MenuItem[] = p.local ? [{ id: "ownstop", label: "Остановить мой показ", icon: "screenStop", onSelect: () => void toggle("screen") }] : [];
      return <ScreenStage p={p} big={s.big} items={() => [...stageMenu(it), ...own, ...moderationItems(p, tileActions, "screen")]} onOpen={() => st.openLarge(it.key)} />;
    }
    return <ParticipantTile p={p} compact={!s.big && st.choice.mode !== "grid"} actions={tileActions} meetingId={guest ? undefined : join.meeting_id} avatarUrl={avatars[p.identity]}
                            localMuted={localMuted.has(p.identity)} onLocalMute={toggleLocalMuted} stageItems={() => stageMenu(it)} onOpen={() => openOrBack(it.key)}
                            onCard={p.local ? undefined : (v) => setCardOf({ identity: v.identity, name: v.name, role: v.leader ? "Руководитель" : v.floor ? "Есть слово" : undefined })} />;
  };
  const badges = (it: StageItem) => (
    <>
      {pinSet.has(it.key) && <span className="st-badge pin" title="Закреплено у вас (видно только вам)" role="img" aria-label="Закреплено у вас"><Icon name="pin" size={13} /></span>}
      {spotSet.has(it.key) && <span className="st-badge spot" title={`Ведущий показывает всем${st.spotBy ? `: ${st.spotBy}` : ""}`}><Icon name="spot" size={13} /> Всем</span>}
    </>
  );
  const layoutItems = (): MenuItem[] => [
    ...LAYOUTS.map((l) => ({ id: `l-${l.id}`, label: `${st.personal.layout === l.id ? "✓ " : "  "}${l.label}`, hint: l.hint, onSelect: () => st.setLayout(l.id) })),
    { id: "reset", label: "Сбросить мои закрепления", icon: "unpin" as const, hidden: !st.personal.pins.length, onSelect: st.resetPins },
    { id: "follow", label: "Смотреть общую сцену (как у ведущего)", icon: "spot" as const, hidden: !(spotTitles.length && st.choice.reason !== "spotlight"), onSelect: st.follow },
    { id: "clearspot", label: "Очистить общую сцену", icon: "eyeOff" as const, hidden: !canSpot || !st.spot.length, onSelect: () => spotlight([]) },
  ];
  const swipe = (dir: 1 | -1) => {
    const keys = st.items.map((i) => i.key);
    if (!keys.length) return;
    const at = Math.max(0, keys.indexOf(st.choice.main[0] ?? keys[0]));
    st.openLarge(keys[(at + dir + keys.length) % keys.length]);
  };
  const focus = focusOn;       // личный режим: правая панель скрыта, доска на всю площадь, участники — узкой лентой
  const boardVisible = focus || (st.choice.main.includes("board") && st.choice.mode !== "grid");
  const stageChoice = focus ? { main: ["board"], reason: "pins" as const, mode: "stage" as const } : st.choice;
  const stageRest = focus ? stripOrder(st.items, ["board"]) : st.rest;
  const leaveFocus = () => setBoardFocus(false);
  const openDevices = (e: React.MouseEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setDevPop((cur) => (cur ? null : { left: Math.max(8, Math.min(r.left - 120, window.innerWidth - 376)), bottom: window.innerHeight - r.top + 8 }));
  };
  /** «Ещё»: то, что нужно реже основных кнопок. Права проверяет сервер; недоступное этому пользователю не показывается. */
  const moreItems = (): MenuItem[] => [
    { id: "devices", label: "Устройства…", icon: "sliders", hint: "Микрофон, динамики и камера", hidden: !roomRef.current, onSelect: () => setDevPop({ left: Math.max(8, window.innerWidth - 392), bottom: 96 }) },
    { id: "noise", label: `${micPrefs.noiseSuppression ? "✓ " : ""}Шумоподавление`, icon: micPrefs.noiseSuppression ? "noise" : "noiseOff", hint: "Убирает фоновый шум микрофона", hidden: !canMic, disabled: stage !== "ready", onSelect: toggleNoise },
    { id: "saudio", label: `${withAudio ? "✓ " : ""}Показ экрана со звуком`, icon: "screen", hint: "Звук вкладки или системы при следующем показе экрана", hidden: !(showScreen && join.client.screen_share_audio) || !!me?.screen, onSelect: () => setWithAudio((v) => !v) },
    ...PROFILE_ORDER.map((pr) => ({ id: `sp-${pr}`, label: `${curProfile === pr ? "✓ " : ""}Показ экрана: ${PROFILE_LABELS[pr]}`, icon: "screen" as const,
      hint: pr === "eco" ? "1280×720, 10 к/с, до 800 кбит/с: слайды читаются, трафик минимальный. Меняется и во время показа" : "Профиль запоминается в этом браузере; во время показа применяется без остановки то, что позволяет браузер",
      hidden: !(showScreen && canScreen), onSelect: () => void chooseProfile(pr) })),
    { id: "rec", label: recording ? "Остановить запись" : "Начать запись", icon: recording ? "recordStop" : "record", hint: recording ? "Остановить запись звука встречи (транскрибация не меняется)" : "Начать запись звука встречи", hidden: !(join.client.recording_allowed && join.client.can_control && !guest), onSelect: () => void toggleRecording() },
    { id: "tr", label: transcribing ? "Остановить транскрибацию" : "Возобновить транскрибацию", icon: transcribing ? "transcriptOff" : "transcript", hint: "Звонок и запись звука продолжаются", hidden: !(room.transcription_enabled && join.client.can_control && !guest), onSelect: () => void toggleTranscription() },
    { id: "endall", label: "Завершить для всех…", icon: "power", danger: true, hidden: !(mobile && join.client.can_control && !guest), onSelect: () => setConfirmEnd(true) },
    { id: "muteall", label: "Выключить у всех микрофоны", icon: "micOff", hint: "У вас — нет. Каждый сможет включить свой снова", hidden: !join.client.can_moderate, disabled: stage !== "ready", onSelect: () => void moderate() },
  ];
  const connLabel = rejoin ? `Переподключение (попытка ${rejoin.attempt} из ${MAX_REJOIN})…`
    : stage !== "ready" ? "Подключение…"
    : state === ConnectionState.Connected ? "Подключено" : state === ConnectionState.Reconnecting ? "Переподключение…" : "Нет соединения";
  const connOk = stage === "ready" && !rejoin && state === ConnectionState.Connected;
  const style = { "--tw": `${tCollapsed ? 0 : tw}px` } as CSSProperties;

  return (
    <div className={`room-wrap ${tCollapsed ? "tcollapsed" : ""} ${focus ? "board-focus" : ""}`} style={style}>
      <section className={`stage ${recording && !ended ? "is-recording" : ""}`}>
        <div className="room-head row">
          <h1>{room.name}</h1>
          {room.lifetime === "temporary" && <span className="badge" title="Комната существует, пока идёт встреча, и закрывается сама через несколько минут после выхода всех. Материалы остаются в «Истории»">Временная переговорка</span>}
          <span className={`badge ${connOk ? "ok" : "warn"}`}>{connLabel}</span>
          {guest && <span className="badge guest" title="Вы вошли по гостевой ссылке: функции управления встречей недоступны">Гость: {guest.info.display_name}</span>}
          {presentation && <span className="badge" title="Участники слушают; говорят руководители и те, кому дали слово">Презентация</span>}
          {presentation && <span className="badge" title="Сколько зрителей сейчас в комнате (без выступающих)">{audienceLabel(audienceView.length)}</span>}
          {presentation && myFloor && !isLeader && <span className="badge ok">У вас слово</span>}
          {recording && <span className="rec-badge on" title="Идёт запись звука встречи"><span className="rec-dot" aria-hidden /> ИДЁТ ЗАПИСЬ</span>}
          {room.transcription_enabled && (
            <span className={`rec-badge ${transcribing ? "tr" : "off"}`} title={transcribing ? "Реплики участников записываются в стенограмму" : "Транскрибация приостановлена руководителем: звонок и запись звука продолжаются"}>
              {transcribing ? "Транскрибация идёт" : "Транскрибация остановлена"}
            </span>
          )}
          {room.transcription_enabled && !asrReady && !guest && <span className="badge warn">{asrLost ? "Транскрибация временно недоступна" : "Транскрибация запускается…"}</span>}
          <div className="spacer" />
          {room.lifetime === "temporary" && !guest && (
            <button className="btn mini" onClick={() => void copyText(`${window.location.origin}/rooms/${room.slug}`)}
                    title="Адрес комнаты: коллеги, которым вы дали доступ («Настройки комнаты» → доступ), смогут войти по нему">Скопировать ссылку</button>
          )}
          {isLeader && <button className="btn mini" onClick={() => setPhoneOpen(true)} aria-label="Позвонить на телефон" title="Позвонить на телефон через SIP: абонент подключится к встрече"><Icon name="phone" size={15} /></button>}
          {isLeader && (
            <button className="btn mini" aria-label="Настройки" aria-haspopup="menu" title="Эта встреча и настройки комнаты — для руководителя"
                    onClick={(e) => { const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); menu.openAt(r.right - 220, r.bottom + 6, [
                      { id: "mset", label: "Эта встреча…", icon: "sliders", hint: "Рассылка материалов и языковая модель только для этой встречи", onSelect: () => setMeetingSettingsOpen(true) },
                      { id: "rset", label: "Настройки комнаты…", icon: "gear", hint: "Постоянные настройки комнаты: доступ, режим, запись, материалы, телефония", onSelect: () => setManageOpen(true) },
                    ]); }}><Icon name="gear" size={15} /></button>
          )}
        </div>

        {ended && (
          <div className="alert ok ended" role="status">
            Встреча завершена.{!guest && redirectIn !== null && redirectIn > 0 ? ` Переход к протоколу через ${redirectIn} с…` : ""}
            {guest
              ? <button className="btn primary mini" onClick={() => guest.onLeft("ended")}>Закрыть</button>
              : <button className="btn primary mini" onClick={() => navigate(`/history/${join.meeting_id}`)}>Перейти к протоколу</button>}
            {!guest && redirectIn !== null && redirectIn > 0 && <button className="btn mini" onClick={() => setRedirectIn(null)}>Остаться здесь</button>}
          </div>
        )}
        {ctlErr.general && (
          <div className="alert error" role="alert">
            {ctlErr.general}
            {!rejoin && !ended && <button className="btn mini" onClick={() => { setErr("general", undefined); scheduleRejoin(0); }}>Войти снова</button>}
          </div>
        )}
        {rejoin && <div className="alert" role="status">Связь потеряна. Восстанавливаем подключение (попытка {rejoin.attempt} из {MAX_REJOIN}). Комната и встреча сохранены.</div>}
        {audioBlocked && !ended && (
          <div className="alert" role="status">Браузер заблокировал звук собеседников. <button className="btn mini primary" onClick={enableAudio}>Включить звук</button>
            {ctlErr.audio && <span className="small"> {ctlErr.audio}</span>}</div>
        )}

        {stage !== "ready" && !ended && <ConnectProgress stage={stage} elapsedMs={tl.stageMs(stage)} done={done} />}

        {/* Сцена: высота постоянна, всё всплывающее (руки, уведомления, баннер общей сцены) — поверх неё, поэтому панель управления не прыгает */}
        <StageView items={st.items} choice={stageChoice} rest={stageRest} mobile={mobile} dense={focus} render={renderItem} badges={badges} label="Сцена встречи"
                   onSwipe={swipe} onEscape={focus ? leaveFocus : st.personal.pins.length ? st.resetPins : undefined}
                   board={boardMounted && !ended && canViewBoard ? () => (
                     <Whiteboard meetingId={join.meeting_id} bus={bus} open={boardVisible} readOnly={!canBoard} fileBase={fileBaseName(room.name, new Date().toISOString())}
                                 onClose={closeBoard} onRemoteChange={(by) => setBoardNews(by || "участник")}
                                 headExtra={<>
                                   <button className="btn mini" onClick={() => st.togglePin("board")} title="Закрепить доску на своей основной сцене (видно только вам)">{pinSet.has("board") ? "Открепить у себя" : "Закрепить у себя"}</button>
                                   {canSpot && <button className="btn mini" onClick={() => spotlight(st.spot.includes("board") ? st.spot.filter((k) => k !== "board") : ["board"])}
                                                       title="Показать доску крупно всем участникам">{st.spot.includes("board") ? "Убрать с общей сцены" : "Показать всем"}</button>}
                                   <button className="btn mini primary" onClick={() => setBoardFocus((f) => !f)} aria-pressed={focus}
                                           title={focus ? "Вернуться к встрече: снова показать участников и правую панель" : "Развернуть доску на всё окно: только у вас, остальные не затронуты"}>{focus ? "Вернуться к встрече" : "Развернуть"}</button>
                                 </>} />
                   ) : undefined}
                   overlay={<>
                     {hands.length > 0 && (
                       <div className="hands-bar" role="status" aria-live="polite">
                         <span className="hands-title"><Icon name="hand" size={15} /> Подняли руку</span>
                         <ol>
                           {hands.map((h) => (
                             <li key={h.identity}><b>{h.name || participants.find((p) => p.identity === h.identity)?.name || "Участник"}</b>
                               {(isLeader || join.client.can_moderate) && !guest && <button type="button" className="hands-x" onClick={() => lowerHand(h.identity)} aria-label="Опустить руку" title="Опустить руку">✕</button>}</li>
                           ))}
                         </ol>
                         <button type="button" className="btn mini ghost" onClick={() => { setHandSoundEnabled(!handSound); setHandSound(!handSound); }}
                                 title="Звуковое уведомление, когда кто-то поднимает руку" aria-pressed={handSound}>{handSound ? "🔔 Звук включён" : "🔕 Звук выключен"}</button>
                       </div>
                     )}
                     {spotTitles.length > 0 && (
                       <div className={`st-spotbar ${st.choice.reason === "spotlight" ? "following" : "own"}`} role="status">
                         <Icon name="spot" size={14} /> <span>{st.spotBy || "Ведущий"} показывает всем: <b>{spotTitles.join(", ")}</b></span>
                         {st.choice.reason === "spotlight"
                           ? (canSpot ? <button className="btn mini ghost" onClick={() => spotlight([])}>Очистить</button>
                                      : <button className="btn mini ghost" onClick={st.ignoreSpot} title="Смотреть свою раскладку; к общей сцене можно вернуться через кнопку «Вид»">Вернуться к своему виду</button>)
                           : <button className="btn mini" onClick={st.follow} title="Сбросить свои закрепления и смотреть то же, что все">Смотреть общую сцену</button>}
                       </div>
                     )}
                     {participants.length === 0 && stage === "ready" && <div className="st-empty muted">Участники появятся здесь.</div>}
                     {presentation && stage === "ready" && participants.length > 0 && split.stage.length === 0 && !ended && <div className="st-wait muted">Ожидаем выступающего: руководитель ещё не в комнате.</div>}
                     {audOpen && presentation && !ended && (
                       <AudiencePanel people={audienceView} canManage={isLeader} onClose={() => setAudOpen(false)}
                                      onGrant={(p) => void giveFloor(p, true)} onLowerHand={(p) => lowerHand(p.identity)}
                                      onCard={(p) => { if (!p.local) setCardOf({ identity: p.identity, name: p.name, role: p.leader ? "Руководитель" : p.floor ? "Есть слово" : undefined }); }} />
                     )}
                     <div className="st-toasts">
                       {welcome && !ended && <div className="alert info welcome" role="status">{welcome} <button className="btn mini ghost" onClick={() => setWelcome(null)}>Скрыть</button></div>}
                       {notice && !ended && <div className={`alert ${notice.kind === "ok" ? "ok" : notice.kind === "warn" ? "error" : "info"}`} role="status">{notice.text} <button className="btn mini ghost" onClick={() => setNotice(null)}>Закрыть</button></div>}
                       {connectTry && stage !== "ready" && <div className="alert" role="status">Соединение не установилось с первого раза — повторная попытка {connectTry.attempt} из {connectTry.max}…</div>}
                     </div>
                   </>} />
        {menu.node}
        <div className="controls rbar" role="toolbar" aria-label="Управление встречей">
         <div className="rbar-main">
          <Ctl error={ctlErr.mic} onClose={() => setErr("mic")}>
            <RoundButton icon={me?.mic ? "mic" : "micOff"} label={!canMic ? "Слушаете" : me?.mic ? "Микрофон" : "Микрофон выкл."} short={!canMic ? "Слушаете" : "Микрофон"} tone={me?.mic ? "on" : "off"} pressed={!!me?.mic} pulse={!!me?.mic && !!me?.speaking}
                         title={!canMic ? listenerHint : me?.mic ? "Выключить микрофон" : "Включить микрофон"} disabled={ended || stage !== "ready" || !canMic} onClick={() => { setMicFail(null); void toggle("mic"); }}
                         onMore={roomRef.current ? openDevices : undefined} moreLabel="Выбор микрофона, динамиков и камеры" />
            {micFail && !ended && (
              <div className="row tight small">
                <button className="btn mini primary" onClick={() => { setErr("mic", undefined); setMicFail(null); void enableMic(); }}>Повторить</button>
                {micFail === "busy" && <span className="muted">Устройство занято — закройте другую программу или выберите другой микрофон через стрелку у кнопки.</span>}
              </div>
            )}
          </Ctl>
          {showCam && (
            <Ctl error={ctlErr.cam} onClose={() => setErr("cam")}>
              <RoundButton icon={me?.cam ? "video" : "videoOff"} label={me?.cam ? "Камера" : "Камера выкл."} short="Камера" tone={me?.cam ? "on" : "off"} pressed={!!me?.cam}
                           title={!canCam ? listenerHint : me?.cam ? "Выключить камеру" : "Включить камеру"} disabled={ended || stage !== "ready" || !canCam} onClick={() => toggle("cam")}
                           onMore={roomRef.current ? openDevices : undefined} moreLabel="Выбор микрофона, динамиков и камеры" />
            </Ctl>
          )}
          {showScreen && (
            <Ctl error={ctlErr.screen} onClose={() => setErr("screen")}>
              <RoundButton icon={me?.screen ? "screenStop" : "screen"} label={me?.screen ? "Остановить показ" : "Показать экран"} short={me?.screen ? "Стоп" : "Экран"} tone={me?.screen ? "live" : "neutral"} pressed={!!me?.screen}
                           title={!canScreen ? listenerHint : me?.screen ? "Остановить показ экрана" : `Выберите экран, окно или вкладку — трансляция начнётся сразу${join.client.screen_share_audio && withAudio ? " (со звуком)" : ""}`} disabled={ended || stage !== "ready" || !canScreen} onClick={() => toggle("screen")} />
            </Ctl>
          )}
          {canViewBoard && <Ctl onClose={() => undefined}>
            <RoundButton icon="board" label={boardNews && !boardVisible ? "Доска · обновлена" : "Доска"} short={boardNews && !boardVisible ? "Доска ●" : "Доска"} tone={boardVisible ? "on" : "neutral"} pressed={boardVisible} disabled={ended}
                         title={canBoard ? "Общая доска для схем: рисуют участники, схема сохраняется со встречей" : "Общая доска: вы можете смотреть. Править — руководитель или тот, кому дали слово"}
                         onClick={() => { setBoardMounted(true); setBoardNews(null); if (boardVisible) closeBoard(); else { setBoardOpen(true); st.openLarge("board"); } }} />
          </Ctl>}
          <Ctl onClose={() => undefined}>
            <RoundButton icon="layout" label="Вид" tone={st.personal.layout !== "auto" || st.personal.pins.length ? "on" : "neutral"} disabled={ended}
                         title="Вид сцены: авто, сетка, сцена, рядом; сбросить закрепления. Меняет раскладку только у вас"
                         onClick={(e) => { const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); menu.openAt(r.left, r.top - 8, layoutItems()); }} />
          </Ctl>
          <Ctl onClose={() => undefined}>
            <RoundButton icon="hand" label={myHand ? "Опустить руку" : "Поднять руку"} short="Рука" tone={myHand ? "on" : "neutral"} pressed={myHand} disabled={ended}
                         title={myHand ? "Опустить руку" : "Поднять руку: все увидят отметку, а вы встанете в очередь"} onClick={toggleHand} />
          </Ctl>
          {presentation && (
            <Ctl onClose={() => undefined}>
              <RoundButton icon="users" label={`Зрители: ${audienceView.length}`} short="Зрители" tone={audOpen ? "on" : "neutral"} pressed={audOpen} disabled={ended}
                           title="Список зрителей: число, поиск по имени, поднятые руки. Руководитель даёт слово отсюда" onClick={() => setAudOpen((o) => !o)} />
            </Ctl>
          )}
          <Ctl onClose={() => undefined}>
            <RoundButton icon="chat" label="Чат" tone="neutral" title="Открыть чат встречи" disabled={ended} onClick={() => { setTCollapsed(false); lsSet("room.tcollapsed", "0"); setChatSignal((n) => n + 1); setBoardFocus(false); }} />
          </Ctl>
          <Ctl error={ctlErr.rec ?? ctlErr.tr} onClose={() => { setErr("rec"); setErr("tr"); }}>
            <RoundButton icon="more" label="Ещё" tone={recording ? "rec" : "neutral"} badge={recording} disabled={ended}
                         title="Шумоподавление, устройства, запись, транскрибация и другие действия"
                         onClick={(e) => { const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); menu.openAt(r.left, r.top - 8, moreItems()); }} />
          </Ctl>
         </div>
         <div className="rbar-end">{/* завершающие действия — отдельная подгруппа; подтверждение — окном, а не вставкой в панель, чтобы раскладка не прыгала */}
          {guest || !join.client.can_control || mobile
            ? (mobile ? null : <div className="rbar-slot" aria-hidden />)
            : <RoundButton icon="power" label="Завершить для всех" short="Завершить" tone="neutral" title="Завершить встречу для всех участников" disabled={ended} onClick={() => setConfirmEnd(true)} />}
          <RoundButton icon="hangup" label="Выйти" tone="danger" title="Выйти из комнаты (встреча продолжится у остальных)" onClick={leave} />
         </div>
        </div>
        {devPop && roomRef.current && (
          <div className="dev-pop" role="dialog" aria-label="Устройства" style={{ left: devPop.left, bottom: devPop.bottom }}>
            <div className="dev-head"><span>Устройства</span><button type="button" className="icon-btn" onClick={() => setDevPop(null)} aria-label="Закрыть"><Icon name="close" size={16} /></button></div>
            <DevicePanel room={roomRef.current} prefs={micPrefs} onPrefs={applyMicPrefs} inline />
          </div>
        )}
        {confirmEnd && <ConfirmDialog title="Завершить встречу для всех?" confirmLabel="Да, завершить" onClose={() => setConfirmEnd(false)}
                                      body={<p>Встреча закончится у всех участников. Стенограмма и материалы сохранятся.</p>} onConfirm={endForAll} />}
        {ctlErr.device && <div className="alert error" role="alert">Устройство: {ctlErr.device} <button className="btn mini" onClick={() => setErr("device")}>Закрыть</button></div>}
        <div ref={audioBox} className="hidden-audio" aria-hidden />
      </section>
      <div className="splitter" role="separator" aria-orientation="vertical" aria-label="Изменить ширину транскрипции (стрелки влево/вправо)" tabIndex={0}
           onPointerDown={tCollapsed ? undefined : onSplitDown} onKeyDown={tCollapsed ? undefined : onSplitKey} hidden={tCollapsed} />
      {cardOf && <ParticipantCardDialog meetingId={join.meeting_id} identity={cardOf.identity} name={cardOf.name} role={cardOf.role} localMuted={localMuted.has(cardOf.identity)} onToggleLocalMute={() => toggleLocalMuted(cardOf.identity)} onClose={() => setCardOf(null)} />}
      {manageOpen && <Suspense fallback={null}><RoomManageDialog roomId={room.id} onClose={() => setManageOpen(false)} /></Suspense>}
      {meetingSettingsOpen && <Suspense fallback={null}><MeetingSettingsDialog meetingId={join.meeting_id} roomId={room.id} onClose={() => setMeetingSettingsOpen(false)} /></Suspense>}
      {phoneOpen && <Suspense fallback={null}><PhoneDialog roomId={room.id} onClose={() => setPhoneOpen(false)} /></Suspense>}
      <TranscriptPanel meetingId={join.meeting_id} enabled={room.transcription_enabled} paused={!transcribing} canAttach={join.client.attachments !== false} asrReady={asrReady} asrLost={asrLost} collapsed={tCollapsed}
                       onToggleCollapsed={toggleCollapsed} onMeetingEnded={onMeetingEnded} onEvent={onLive} onStatus={onSocketStatus}
                       bus={bus} guestToken={guest ? guest.info.guest_token : null} selfName={guest ? `${guest.info.display_name} (гость)` : selfName} openChatSignal={chatSignal} />
    </div>
  );
}
