import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ConnectionState, DisconnectReason, Participant, Room as LkRoom, RoomEvent, Track, createLocalAudioTrack, type LocalAudioTrack } from "livekit-client";
import { api, ApiError, leaveOnUnload, type JoinInfo } from "../api";
import ConnectProgress from "../components/room/ConnectProgress";
import DebugPanel from "../components/room/DebugPanel";
import { ParticipantTile, ScreenStage, type PView } from "../components/room/Tiles";
import DevicePanel from "../components/DevicePanel";
import TranscriptPanel from "../components/TranscriptPanel";
import { FreezeDetector, JoinTimeline, RateMeter, metricsBody, newInstanceId, reportEvent, reportRoomPhase, reportScreenPhase, sampleRoom, type RoomPhase, type ScreenPhase, type Snapshot, type Stage } from "../diagnostics";
import { MIC_CAPTURE, buildRoomOptions } from "../roomOptions";
import type { LiveEvent, SocketStatus } from "../liveSocket";
import { backoffDelay } from "../liveSocket";
import { describeMediaError, SCREEN_STOP_TEXT, type MediaAction, type ScreenStopReason } from "../mediaErrors";
import { isScreenProfile, screenShareOptions } from "../screenShare";

type CtlKey = "mic" | "cam" | "screen" | "rec" | "device" | "audio" | "general";
type CtlErrors = Partial<Record<CtlKey, string>>;

const MAX_REJOIN = 6;
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

export default function RoomPage() {
  const { roomId = "" } = useParams();
  const navigate = useNavigate();
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
    if (notify && meetingRef.current) await api.leave(meetingRef.current).catch(() => undefined);
    meetingRef.current = null;
    setParticipants([]);
  }, []);

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
    reportEvent("join_ok", { meetingId: meetingRef.current, detail: JSON.stringify(timeline.current.metrics()) });
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
        await lp.setMicrophoneEnabled(true, MIC_CAPTURE);
      }
      tl.mark("micPublished");
      micWantedRef.current = true;
      setErr("mic", undefined);
      refresh();
    } catch (e) {
      fail("mic", "mic", "mic_failed", e);
      setErr("mic", `${describeMediaError(e, "mic").message} Вы остаётесь в комнате без звука.`);
    }
    sendJoinReport();
  }, [fail, refresh, sendJoinReport, setErr]);

  const connectLivekit = useCallback(async (info: JoinInfo, kind: "initial" | "rejoin") => {
    const tl = timeline.current;
    setStage("server");
    const room = new LkRoom(buildRoomOptions());
    instanceRef.current = newInstanceId();
    setInstance(instanceRef.current);
    setRoomsCreated((n) => n + 1);
    roomRef.current = room;
    tl.mark("roomCreated");
    phase("ROOM_CREATE", kind === "rejoin" ? "reason=rejoin" : "reason=initial");
    if (kind === "initial") tl.mark("connectStart");
    phase("CONNECT_START");
    let connectedOnce = false; // отказ ПЕРВОГО подключения обрабатывает вызывающий код; автоматический повторный вход — только после успешного
    room
      .on(RoomEvent.ParticipantConnected, refresh).on(RoomEvent.ParticipantDisconnected, refresh)
      .on(RoomEvent.TrackMuted, refresh).on(RoomEvent.TrackUnmuted, refresh)
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
        const info = await api.join(roomId, pwRef.current); // свежий токен: прежний мог устареть
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
  }, [connectLivekit, dlog, enableMic, roomId, setErr]);

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
      info = await api.join(roomId, pw);
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
    setJoin(info); // комната и канал событий открываются сразу — параллельно с подключением к LiveKit
    setBusy(false);
    // Критический путь — Room.connect. Всё независимое идёт рядом: микрофон (getUserMedia, включая запрос разрешения) начинается
    // немедленно и не ждёт подключения; канал событий (WebSocket) открывает TranscriptPanel при появлении комнаты.
    tl.mark("gumStart");
    prepMicRef.current = createLocalAudioTrack(MIC_CAPTURE).then((t) => { tl.mark("gumEnd"); return t; }, (e: unknown) => { tl.mark("gumEnd"); return e instanceof Error ? e : new Error(String(e)); });
    try {
      await connectLivekit(info, "initial");
      void enableMic();
    } catch (e) {
      const m = describeMediaError(e, "connect");
      reportEvent("join_failed", { meetingId: info.meeting_id, reason: m.reason, detail: String((e as Error)?.message ?? e) });
      await teardown(true);
      setJoin(null);
      setError(m.message);
    }
  };

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
  const leave = async () => { await teardown(true); navigate("/"); };
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
    setRedirectIn(6);
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
          {room.transcription_enabled && (
            <span className={`rec-badge ${recording ? "on" : "off"}`} title={recording ? "Идёт запись и транскрибация встречи" : "Запись остановлена"}>
              {recording ? <><span className="rec-dot" aria-hidden /> ИДЁТ ЗАПИСЬ</> : "Запись остановлена"}
            </span>
          )}
          {room.transcription_enabled && !asrReady && <span className="badge warn">{asrLost ? "Транскрибация временно недоступна" : "Транскрибация запускается…"}</span>}
          <div className="spacer" />
          <button className={`btn mini ${debug ? "primary" : ""}`} onClick={toggleDebug} title="Тайминги входа, статистика соединения и показа экрана">⚙ Диагностика</button>
        </div>

        {ended && (
          <div className="alert ok ended" role="status">
            Встреча завершена.{redirectIn !== null && redirectIn > 0 ? ` Переход к протоколу через ${redirectIn} с…` : ""}
            <button className="btn primary mini" onClick={() => navigate(`/history/${join.meeting_id}`)}>Перейти к протоколу</button>
            {redirectIn !== null && redirectIn > 0 && <button className="btn mini" onClick={() => setRedirectIn(null)}>Остаться здесь</button>}
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
        <div className={`tiles n${n} ${sharer ? "strip" : ""}`}>
          {participants.map((p) => <ParticipantTile key={p.identity} p={p} compact={!!sharer} />)}
          {participants.length === 0 && stage === "ready" && <div className="muted">Участники появятся здесь.</div>}
        </div>

        <div className="controls">
          <Ctl error={ctlErr.mic} onClose={() => setErr("mic")}>
            <button className={`btn ${me?.mic || stage !== "ready" ? "" : "danger"}`} onClick={() => toggle("mic")} disabled={ended || stage !== "ready"}>{me?.mic ? "🎙 Выключить микрофон" : "🔇 Включить микрофон"}</button>
          </Ctl>
          {room.camera_allowed && (
            <Ctl error={ctlErr.cam} onClose={() => setErr("cam")}>
              <button className={`btn ${me?.cam ? "primary" : ""}`} onClick={() => toggle("cam")} disabled={ended || stage !== "ready"}>{me?.cam ? "📷 Выключить камеру" : "🚫 Включить камеру"}</button>
            </Ctl>
          )}
          {room.screen_share_allowed && (
            <Ctl error={ctlErr.screen} onClose={() => setErr("screen")}>
              <div className="row tight">
                <button className={`btn ${me?.screen ? "primary" : "accent"}`} onClick={() => toggle("screen")} disabled={ended || stage !== "ready"}
                        title="Выберите экран, окно или вкладку — трансляция начнётся сразу">
                  {me?.screen ? "■ Остановить показ" : "🖥 Показать экран"}
                </button>
                {join.client.screen_share_audio && !me?.screen && (
                  <label className="check small"><input type="checkbox" checked={withAudio} onChange={(e) => setWithAudio(e.target.checked)} /> со звуком</label>
                )}
              </div>
            </Ctl>
          )}
          {room.transcription_enabled && (
            <Ctl error={ctlErr.rec} onClose={() => setErr("rec")}>
              <button className="btn" onClick={toggleRecording} disabled={ended}>{recording ? "Остановить запись" : "● Начать запись"}</button>
            </Ctl>
          )}
          <div className="spacer" />
          {!confirmEnd
            ? <button className="btn ghost" onClick={() => setConfirmEnd(true)} disabled={ended}>Завершить для всех</button>
            : <><span className="muted small">Завершить встречу для всех?</span>
                <button className="btn danger" onClick={endForAll}>Да, завершить</button>
                <button className="btn ghost" onClick={() => setConfirmEnd(false)}>Отмена</button></>}
          <button className="btn danger" onClick={leave}>Выйти</button>
        </div>
        {ctlErr.device && <div className="alert error" role="alert">Устройство: {ctlErr.device} <button className="btn mini" onClick={() => setErr("device")}>Закрыть</button></div>}
        {roomRef.current && <DevicePanel room={roomRef.current} />}
        {debug && <DebugPanel snapshot={snapshot} join={tl.metrics()} connection={`${state}${rejoin ? ` · повторный вход ${rejoin.attempt}` : ""}`} socket={socket} asrReady={asrReady} log={log} instance={instance} roomsCreated={roomsCreated} />}
        <div ref={audioBox} className="hidden-audio" aria-hidden />
      </section>
      <div className="splitter" role="separator" aria-orientation="vertical" aria-label="Изменить ширину транскрипции (стрелки влево/вправо)" tabIndex={0}
           onPointerDown={tCollapsed ? undefined : onSplitDown} onKeyDown={tCollapsed ? undefined : onSplitKey} hidden={tCollapsed} />
      <TranscriptPanel meetingId={join.meeting_id} enabled={room.transcription_enabled} asrReady={asrReady} asrLost={asrLost} collapsed={tCollapsed}
                       onToggleCollapsed={toggleCollapsed} onMeetingEnded={onMeetingEnded} onEvent={onLive} onStatus={onSocketStatus} />
    </div>
  );
}
