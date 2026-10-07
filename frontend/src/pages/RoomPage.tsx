import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ConnectionState, DisconnectReason, Participant, Room as LkRoom, RoomEvent, Track, createLocalAudioTrack, type LocalAudioTrack } from "livekit-client";
import { api, ApiError, leaveOnUnload, type GuestJoinInfo, type JoinInfo } from "../api";
import Whiteboard from "../board/Whiteboard";
import ConnectProgress from "../components/room/ConnectProgress";
import DebugPanel from "../components/room/DebugPanel";
import { ParticipantTile, ScreenStage, type PView } from "../components/room/Tiles";
import RoundButton from "../components/room/RoundButton";
import DevicePanel from "../components/DevicePanel";
import TranscriptPanel from "../components/TranscriptPanel";
import { FreezeDetector, JoinTimeline, RateMeter, metricsBody, newInstanceId, reportEvent, reportRoomPhase, reportScreenPhase, sampleRoom, type RoomPhase, type ScreenPhase, type Snapshot, type Stage } from "../diagnostics";
import { buildRoomOptions } from "../roomOptions";
import { captureOptions, loadMicPrefs, saveMicPrefs, type MicPrefs } from "../micPrefs";
import { collectAll } from "../clientInfo";
import type { LiveEvent, SocketStatus } from "../liveSocket";
import { LiveBus, backoffDelay } from "../liveSocket";
import { fileBase as fileBaseName } from "../util";
import { setActiveMeeting } from "../activeMeeting";
import { takePreJoin } from "../prejoin";
import { describeMediaError, isDeviceBusyError, isTransientConnectError, SCREEN_STOP_TEXT, type MediaAction, type ScreenStopReason } from "../mediaErrors";
import { isScreenProfile, screenShareOptions } from "../screenShare";

type CtlKey = "mic" | "cam" | "screen" | "rec" | "device" | "audio" | "general";
type CtlErrors = Partial<Record<CtlKey, string>>;

const MAX_REJOIN = 6;
const MAX_CONNECT_TRIES = 3;   // первое подключение: до 3 попыток при сетевых/ICE-сбоях
const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));
const TW_MIN = 260, TW_MAX = 760, TW_DEFAULT = 380;
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

export default function RoomPage({ guest, selfName }: { guest?: GuestSession; selfName?: string }) {
  const { roomId: routeRoomId = "" } = useParams();
  const roomId = guest ? guest.info.room.id : routeRoomId;
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
  const [recording, setRecording] = useState(true);
  const [asrReady, setAsrReady] = useState(true);
  const [participants, setParticipants] = useState<PView[]>([]);
  const [state, setState] = useState<ConnectionState>(ConnectionState.Disconnected);
  const [stage, setStage] = useState<Stage>("prepare");
  const [, setTick] = useState(0);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [ctlErr, setCtlErr] = useState<CtlErrors>({});
  const [withAudio, setWithAudio] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [rejoin, setRejoin] = useState<{ attempt: number } | null>(null);
  const [socket, setSocket] = useState<SocketStatus>({ state: "connecting", attempt: 0 });
  const [debug, setDebug] = useState(() => new URLSearchParams(location.search).get("debug") === "1" || lsGet("room.debug") === "1");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [tw, setTw] = useState(() => clamp(Number(lsGet("room.tw")) || TW_DEFAULT, TW_MIN, TW_MAX));
  const [tCollapsed, setTCollapsed] = useState(() => lsGet("room.tcollapsed") === "1");
  const [redirectIn, setRedirectIn] = useState<number | null>(null);
  const [asrLost, setAsrLost] = useState(false);
  const [roomsCreated, setRoomsCreated] = useState(0);
  const [instance, setInstance] = useState("");
  const [micPrefs, setMicPrefs] = useState<MicPrefs>(() => loadMicPrefs());
  const [micFail, setMicFail] = useState<"busy" | "denied" | "other" | null>(null);
  const [notice, setNotice] = useState<{ kind: "info" | "ok" | "warn"; text: string } | null>(null);
  const [welcome, setWelcome] = useState<string | null>(null);
  const lastMicToggle = useRef(0);
  const [connectTry, setConnectTry] = useState<{ attempt: number; max: number } | null>(null);

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

  const dlog = useCallback((msg: string) => {
    setLog((l) => [...l.slice(-79), `${new Date().toLocaleTimeString("ru-RU")}  ${msg}`]);
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

  const refresh = useCallback(() => {
    const r = roomRef.current;
    if (!r) return;
    const all: Participant[] = [r.localParticipant, ...Array.from(r.remoteParticipants.values())];
    setParticipants(all.map((p) => ({
      identity: p.identity, name: p.name || p.identity, local: p.isLocal, mic: p.isMicrophoneEnabled, cam: p.isCameraEnabled,
      screen: p.isScreenShareEnabled, speaking: p.isSpeaking, participant: p,
    })));
  }, []);

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
      .on(RoomEvent.TrackSubscribed, (track, pub) => {
        if (track.kind === Track.Kind.Audio && audioBox.current) audioBox.current.appendChild(track.attach());
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
      .on(RoomEvent.SignalConnected, () => { tl.mark("signalConnected"); setStage("media"); phase("SIGNALING_CONNECTED"); })
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
        if (reason === DisconnectReason.PARTICIPANT_REMOVED || reason === DisconnectReason.ROOM_DELETED) {
          setErr("general", "Вас отключили от комнаты (встреча закрыта или участник удалён администратором).");
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
  }, [connectLivekit, dlog, enableMic, fetchJoin, setErr]);

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
    setRecording(info.recording);
    setAsrReady(info.asr_ready);
    asrWasReadyRef.current = info.asr_ready;
    setAsrLost(false);
    setWithAudio(info.client.screen_share_audio);
    setNeedPassword(false);
    setWelcome(info.client.welcome_message ?? null);
    setNotice(info.client.mute_on_join ? { kind: "info", text: "В этой переговорке микрофон по умолчанию выключен. Чтобы говорить, нажмите «Микрофон»." } : null);
    setJoin(info); // комната и канал событий открываются сразу — параллельно с подключением к LiveKit
    setActiveMeeting(info.meeting_id); // верхняя панель с этого момента открывает разделы в новых вкладках
    const pre = takePreJoin(); // устройства, выбранные на проверке оборудования (гость)
    setBusy(false);
    // Критический путь — Room.connect. Всё независимое идёт рядом: микрофон (getUserMedia, включая запрос разрешения) начинается
    // немедленно и не ждёт подключения; канал событий (WebSocket) открывает TranscriptPanel при появлении комнаты.
    tl.mark("gumStart");
    // «микрофон по умолчанию выключен» (настройка переговорки): звук не запрашиваем и не включаем — пользователь включит сам
    if (!info.client.mute_on_join) prepMicRef.current = createLocalAudioTrack({ ...captureOptions(loadMicPrefs()), ...(pre?.micId ? { deviceId: pre.micId } : {}) }).then((t) => { tl.mark("gumEnd"); return t; }, (e: unknown) => { tl.mark("gumEnd"); return e instanceof Error ? e : new Error(String(e)); });
    void collectAll().then((data) => reportEvent("join_attempt", { meetingId: info.meeting_id, room: info.room.name, data }));
    try {
      // Первое подключение переживает кратковременные сбои сети/ICE: до MAX_CONNECT_TRIES попыток с нарастающей паузой и свежим токеном
      for (let attempt = 1; ; attempt++) {
        try {
          await connectLivekit(info, attempt === 1 ? "initial" : "retry");
          break;
        } catch (e) {
          const m = describeMediaError(e, "connect");
          if (attempt >= MAX_CONNECT_TRIES || !isTransientConnectError(e) || leavingRef.current) throw e;
          reportEvent("connect_retry", { meetingId: info.meeting_id, room: info.room.name, reason: m.reason, detail: String((e as Error)?.message ?? e), data: { attempt } });
          dlog(`подключение не удалось (${m.reason}), попытка ${attempt + 1} из ${MAX_CONNECT_TRIES}`);
          setConnectTry({ attempt: attempt + 1, max: MAX_CONNECT_TRIES });
          const failed = roomRef.current;
          roomRef.current = null;
          await failed?.disconnect().catch(() => undefined);
          await sleep(1200 * attempt);
          try { info = await fetchJoin(pw); infoRef.current = info; meetingRef.current = info.meeting_id; } catch { /* прежний токен ещё может подойти */ }
        }
      }
      setConnectTry(null);
      if (!info.client.mute_on_join) void enableMic();
      const lk = roomRef.current;
      if (lk && pre?.speakerId) void lk.switchActiveDevice("audiooutput", pre.speakerId).catch(() => undefined);
      if (lk && pre?.camOn && info.room.camera_allowed) void lk.localParticipant.setCameraEnabled(true, pre.camId ? { deviceId: pre.camId } : undefined).then(refresh).catch((e) => fail("camera", "cam", "camera_failed", e));
    } catch (e) {
      const m = describeMediaError(e, "connect");
      setConnectTry(null);
      reportEvent("join_failed", { meetingId: info.meeting_id, room: info.room.name, reason: m.reason, detail: String((e as Error)?.message ?? e) });
      void collectAll().then((data) => reportEvent(m.reason === "IceFailed" ? "ice_failed" : "connect_failed", {
        meetingId: info.meeting_id, room: info.room.name, reason: m.reason, detail: String((e as Error)?.message ?? e), data: { ...data, attempts: MAX_CONNECT_TRIES } }));
      await teardown(true);
      setJoin(null);
      setError(m.message);
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
          const profile = isScreenProfile(join.client.screen_profile) ? join.client.screen_profile : "sharp";
          const o = screenShareOptions(profile, join.client.screen_share_audio && withAudio);
          userStopRef.current = false;
          screenPhase("SCREEN_CREATE", `profile=${profile} audio=${o.capture.audio ? "yes" : "no"}`);
          screenPhase("SCREEN_PUBLISH_START");
          await lp.setScreenShareEnabled(true, o.capture, o.publish);
          reportEvent(screenStoppedOnceRef.current ? "screen_share_restarted" : "screen_share_started", { meetingId: join.meeting_id, detail: `${profile}, audio=${o.capture.audio}` });
        }
      }
    } catch (e) {
      if (what === "screen") { screenIntendedRef.current = false; screenPhase("SCREEN_ERROR", `reason=${(e as Error)?.name ?? "error"}`); fail("screen", "screen", "screen_share_failed", e); }
      else if (what === "mic") fail("mic", "mic", "mic_failed", e);
      else fail("camera", "cam", "camera_failed", e);
    }
    refresh();
  };

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

  const enableAudio = async () => {
    try { await roomRef.current?.startAudio(); setAudioBlocked(!(roomRef.current?.canPlaybackAudio ?? true)); setErr("audio", undefined); }
    catch (e) { fail("playback", "audio", "device_error", e); }
  };

  const onSocketStatus = useCallback((st: SocketStatus) => {
    setSocket(st);
    if (st.state === "connecting") timeline.current.mark("wsStart");
    if (st.state === "online") timeline.current.mark("wsOpen");
  }, []);
  const onLive = useCallback((e: LiveEvent) => { if (e.type === "recording_changed") setRecording(e.enabled); }, []);
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
  const toggleDebug = () => setDebug((d) => { lsSet("room.debug", d ? "0" : "1"); return !d; });

  // ---------------------------------------------------------------------- вид «до входа»
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
      <section className="prejoin card">
        <h1>Вход в комнату</h1>
        <p className="muted">Микрофон включится сразу после входа. Реплики участников записываются в протокол встречи.</p>
        {needPassword && (
          <label>Пароль комнаты
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus
                   onKeyDown={(e) => { if (e.key === "Enter" && password && !busy) void connect(password); }} />
          </label>
        )}
        {error && <div className="alert error" role="alert">{error}</div>}
        <div className="row">
          <button className="btn primary" disabled={busy || (needPassword && !password)} onClick={() => connect(needPassword ? password : undefined)}>
            {busy ? "Вход…" : "Войти в комнату"}
          </button>
          <button className="btn ghost" onClick={() => navigate("/")}>Назад</button>
        </div>
        {busy && <ConnectProgress stage="prepare" elapsedMs={tl.stageMs("prepare")} done={done} />}
      </section>
    );
  }

  const room = join.room;
  const me = participants.find((p) => p.local);
  const sharer = participants.find((p) => p.screen);
  const connLabel = rejoin ? `Переподключение (попытка ${rejoin.attempt} из ${MAX_REJOIN})…`
    : stage !== "ready" ? "Подключение…"
    : state === ConnectionState.Connected ? "Подключено" : state === ConnectionState.Reconnecting ? "Переподключение…" : "Нет соединения";
  const connOk = stage === "ready" && !rejoin && state === ConnectionState.Connected;
  const n = Math.min(participants.length, 9);
  const style = { "--tw": `${tCollapsed ? 44 : tw}px` } as CSSProperties;

  return (
    <div className={`room-wrap ${tCollapsed ? "tcollapsed" : ""}`} style={style}>
      <section className={`stage ${room.transcription_enabled && recording && !ended ? "is-recording" : ""}`}>
        <div className="room-head row">
          <h1>{room.name}</h1>
          <span className={`badge ${connOk ? "ok" : "warn"}`}>{connLabel}</span>
          {guest && <span className="badge guest" title="Вы вошли по гостевой ссылке: функции управления встречей недоступны">Гость: {guest.info.display_name}</span>}
          {room.transcription_enabled && (
            <span className={`rec-badge ${recording ? "on" : "off"}`} title={recording ? "Идёт запись и транскрибация встречи" : "Запись остановлена"}>
              {recording ? <><span className="rec-dot" aria-hidden /> ИДЁТ ЗАПИСЬ</> : "Запись остановлена"}
            </span>
          )}
          {room.transcription_enabled && !asrReady && !guest && <span className="badge warn">{asrLost ? "Транскрибация временно недоступна" : "Транскрибация запускается…"}</span>}
          <div className="spacer" />
          <button className={`btn mini ${debug ? "primary" : ""}`} onClick={toggleDebug} title="Тайминги входа, статистика соединения и показа экрана">⚙ Диагностика</button>
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

        {sharer && <ScreenStage key={sharer.identity} p={sharer} />}
        {boardMounted && !ended && (
          <Whiteboard meetingId={join.meeting_id} bus={bus} open={boardOpen} fileBase={fileBaseName(room.name, new Date().toISOString())}
                      onClose={() => setBoardOpen(false)} onRemoteChange={(by) => setBoardNews(by || "участник")} />
        )}
        <div className={`tiles n${n} ${sharer || boardOpen ? "strip" : ""}`}>
          {participants.map((p) => <ParticipantTile key={p.identity} p={p} compact={!!sharer} onMute={join.client.can_moderate ? moderate : undefined} />)}
          {participants.length === 0 && stage === "ready" && <div className="muted">Участники появятся здесь.</div>}
        </div>

        {welcome && !ended && <div className="alert info welcome" role="status">{welcome} <button className="btn mini ghost" onClick={() => setWelcome(null)}>Скрыть</button></div>}
        {notice && !ended && <div className={`alert ${notice.kind === "ok" ? "ok" : notice.kind === "warn" ? "error" : "info"}`} role="status">{notice.text} <button className="btn mini ghost" onClick={() => setNotice(null)}>Закрыть</button></div>}
        {connectTry && stage !== "ready" && <div className="alert" role="status">Соединение не установилось с первого раза — повторная попытка {connectTry.attempt} из {connectTry.max}…</div>}
        <div className="controls rbar">
          <Ctl error={ctlErr.mic} onClose={() => setErr("mic")}>
            <RoundButton icon={me?.mic ? "mic" : "micOff"} label={me?.mic ? "Микрофон" : "Микрофон выкл."} tone={me?.mic ? "on" : "off"} pressed={!!me?.mic} pulse={!!me?.mic && !!me?.speaking}
                         title={me?.mic ? "Выключить микрофон" : "Включить микрофон"} disabled={ended || stage !== "ready"} onClick={() => { setMicFail(null); void toggle("mic"); }} />
            {micFail && !ended && (
              <div className="row tight small">
                <button className="btn mini primary" onClick={() => { setErr("mic", undefined); setMicFail(null); void enableMic(); }}>Повторить</button>
                {micFail === "busy" && <span className="muted">Устройство занято — закройте другую программу или выберите другой микрофон ниже.</span>}
              </div>
            )}
          </Ctl>
          {room.camera_allowed && (
            <Ctl error={ctlErr.cam} onClose={() => setErr("cam")}>
              <RoundButton icon={me?.cam ? "video" : "videoOff"} label={me?.cam ? "Камера" : "Камера выкл."} tone={me?.cam ? "on" : "off"} pressed={!!me?.cam}
                           title={me?.cam ? "Выключить камеру" : "Включить камеру"} disabled={ended || stage !== "ready"} onClick={() => toggle("cam")} />
            </Ctl>
          )}
          <Ctl onClose={() => undefined}>
            <RoundButton icon={micPrefs.noiseSuppression ? "noise" : "noiseOff"} label="Шумоподавление" tone={micPrefs.noiseSuppression ? "on" : "neutral"} pressed={micPrefs.noiseSuppression}
                         title={micPrefs.noiseSuppression ? "Шумоподавление включено — нажмите, чтобы выключить" : "Шумоподавление выключено — нажмите, чтобы включить"}
                         disabled={ended || stage !== "ready"} onClick={toggleNoise} />
          </Ctl>
          {room.screen_share_allowed && !guest && (
            <Ctl error={ctlErr.screen} onClose={() => setErr("screen")}>
              <RoundButton icon={me?.screen ? "screenStop" : "screen"} label={me?.screen ? "Остановить показ" : "Показать экран"} tone={me?.screen ? "live" : "neutral"} pressed={!!me?.screen}
                           title="Выберите экран, окно или вкладку — трансляция начнётся сразу" disabled={ended || stage !== "ready"} onClick={() => toggle("screen")}>
                {join.client.screen_share_audio && !me?.screen && (
                  <label className="check small"><input type="checkbox" checked={withAudio} onChange={(e) => setWithAudio(e.target.checked)} /> со звуком</label>
                )}
              </RoundButton>
            </Ctl>
          )}
          <Ctl onClose={() => undefined}>
            <RoundButton icon="board" label={boardNews && !boardOpen ? "Доска · обновлена" : "Доска"} tone={boardOpen ? "on" : "neutral"} pressed={boardOpen} disabled={ended}
                         title="Общая доска для схем: рисуют все участники, схема сохраняется со встречей"
                         onClick={() => { setBoardMounted(true); setBoardOpen((o) => !o); setBoardNews(null); }} />
          </Ctl>
          <Ctl onClose={() => undefined}>
            <RoundButton icon="chat" label="Чат" tone="neutral" title="Открыть чат встречи" disabled={ended} onClick={() => { setTCollapsed(false); lsSet("room.tcollapsed", "0"); setChatSignal((n) => n + 1); }} />
          </Ctl>
          {room.transcription_enabled && !guest && (
            <Ctl error={ctlErr.rec} onClose={() => setErr("rec")}>
              <RoundButton icon={recording ? "recordStop" : "record"} label={recording ? "Остановить запись" : "Начать запись"} tone={recording ? "rec" : "neutral"} pressed={recording}
                           disabled={ended} onClick={toggleRecording} />
            </Ctl>
          )}
          {join.client.can_moderate && (
            <Ctl onClose={() => undefined}>
              <RoundButton icon="micOff" label="Выключить у всех" tone="neutral" title="Выключить микрофоны у всех участников (у вас — нет). Каждый сможет включить свой снова"
                           disabled={ended || stage !== "ready"} onClick={() => void moderate()} />
            </Ctl>
          )}
          <div className="spacer" />
          {guest ? null : !confirmEnd
            ? <RoundButton icon="power" label="Завершить для всех" tone="neutral" title="Завершить встречу для всех участников" disabled={ended} onClick={() => setConfirmEnd(true)} />
            : <div className="confirm-end"><span className="muted small">Завершить встречу для всех?</span>
                <div className="row tight"><button className="btn danger" onClick={endForAll}>Да, завершить</button>
                <button className="btn ghost" onClick={() => setConfirmEnd(false)}>Отмена</button></div></div>}
          <RoundButton icon="hangup" label="Выйти" tone="danger" title="Выйти из комнаты (встреча продолжится у остальных)" onClick={leave} />
        </div>
        {ctlErr.device && <div className="alert error" role="alert">Устройство: {ctlErr.device} <button className="btn mini" onClick={() => setErr("device")}>Закрыть</button></div>}
        {roomRef.current && <DevicePanel room={roomRef.current} prefs={micPrefs} onPrefs={applyMicPrefs} />}
        {debug && <DebugPanel snapshot={snapshot} join={tl.metrics()} connection={`${state}${rejoin ? ` · повторный вход ${rejoin.attempt}` : ""}`} socket={socket} asrReady={asrReady} log={log} instance={instance} roomsCreated={roomsCreated} />}
        <div ref={audioBox} className="hidden-audio" aria-hidden />
      </section>
      <div className="splitter" role="separator" aria-orientation="vertical" aria-label="Изменить ширину транскрипции (стрелки влево/вправо)" tabIndex={0}
           onPointerDown={tCollapsed ? undefined : onSplitDown} onKeyDown={tCollapsed ? undefined : onSplitKey} hidden={tCollapsed} />
      <TranscriptPanel meetingId={join.meeting_id} enabled={room.transcription_enabled} asrReady={asrReady} asrLost={asrLost} collapsed={tCollapsed}
                       onToggleCollapsed={toggleCollapsed} onMeetingEnded={onMeetingEnded} onEvent={onLive} onStatus={onSocketStatus}
                       bus={bus} guestToken={guest ? guest.info.guest_token : null} selfName={guest ? `${guest.info.display_name} (гость)` : selfName} openChatSignal={chatSignal} />
    </div>
  );
}
