import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ConnectionState, Participant, Room as LkRoom, RoomEvent, Track } from "livekit-client";
import { api, ApiError, leaveOnUnload, type JoinInfo } from "../api";
import DevicePanel from "../components/DevicePanel";
import TranscriptPanel from "../components/TranscriptPanel";
import type { LiveEvent } from "../liveSocket";
import { isScreenProfile, screenShareOptions } from "../screenShare";

interface PView {
  identity: string; name: string; local: boolean; mic: boolean; cam: boolean; screen: boolean; speaking: boolean; participant: Participant;
}

function VideoTile({ p, source, className = "video", onSize }: {
  p: Participant; source: Track.Source; className?: string; onSize?: (w: number, h: number) => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const track = p.getTrackPublication(source)?.track;
  useEffect(() => {
    const el = ref.current;
    if (!el || !track) return;
    track.attach(el);
    const report = () => onSize?.(el.videoWidth, el.videoHeight);
    el.addEventListener("resize", report);
    el.addEventListener("loadedmetadata", report);
    return () => { el.removeEventListener("resize", report); el.removeEventListener("loadedmetadata", report); track.detach(el); };
  }, [track, onSize]);
  return track ? <video ref={ref} autoPlay playsInline muted={p.isLocal} className={className} /> : null;
}

/** Большая «сцена» демонстрации экрана: полноэкранный режим, «вписать/заполнить», текущее разрешение. */
function ScreenStage({ p }: { p: PView }) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState("");
  const [fill, setFill] = useState(false);
  const onSize = useCallback((w: number, h: number) => setSize(w && h ? `${w}×${h}` : ""), []);
  const fullscreen = () => { const el = box.current; if (!el) return; if (document.fullscreenElement) void document.exitFullscreen(); else void el.requestFullscreen?.(); };
  return (
    <div className={`screen-stage ${fill ? "fill" : ""}`} ref={box} onDoubleClick={fullscreen}>
      <VideoTile p={p.participant} source={Track.Source.ScreenShare} className="screen-video" onSize={onSize} />
      <div className="screen-bar">
        <span>{p.local ? "Вы показываете экран" : `Экран: ${p.name}`}{size && <span className="muted"> · {size}</span>}</span>
        <span className="spacer" />
        <button className="btn mini" onClick={() => setFill((f) => !f)} title="Вписать / заполнить окно">{fill ? "Вписать" : "Заполнить"}</button>
        <button className="btn mini" onClick={fullscreen} title="Полный экран (или двойной клик)">⛶ На весь экран</button>
      </div>
    </div>
  );
}

/** Русские сообщения вместо технических ошибок SDK/сети. */
function describeConnectError(e: unknown): string {
  if (e instanceof ApiError) return e.message || "Не удалось войти в комнату";
  const m = String((e as Error)?.message ?? e).toLowerCase();
  if (m.includes("signal connection") || m.includes("failed to fetch") || m.includes("websocket"))
    return "Не удалось подключиться к серверу звонков. Проверьте сеть и адрес (нужен HTTPS); если проблема сохраняется — сообщите администратору.";
  if (m.includes("permission") || m.includes("notallowed")) return "Браузер не дал доступ к устройствам.";
  if (m.includes("timeout")) return "Сервер звонков не отвечает (таймаут).";
  return "Не удалось подключиться к комнате. Повторите попытку.";
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
  const [participants, setParticipants] = useState<PView[]>([]);
  const [state, setState] = useState<ConnectionState>(ConnectionState.Disconnected);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const roomRef = useRef<LkRoom | null>(null);
  const audioBox = useRef<HTMLDivElement>(null);
  const meetingRef = useRef<string | null>(null);

  const refresh = useCallback(() => {
    const r = roomRef.current;
    if (!r) return;
    const all: Participant[] = [r.localParticipant, ...Array.from(r.remoteParticipants.values())];
    setParticipants(all.map((p) => ({
      identity: p.identity, name: p.name || p.identity, local: p.isLocal, mic: p.isMicrophoneEnabled, cam: p.isCameraEnabled,
      screen: p.isScreenShareEnabled, speaking: p.isSpeaking, participant: p,
    })));
  }, []);

  const teardown = useCallback(async (notify: boolean) => {
    const r = roomRef.current;
    roomRef.current = null;
    await r?.disconnect().catch(() => undefined);
    audioBox.current?.replaceChildren();
    if (notify && meetingRef.current) await api.leave(meetingRef.current).catch(() => undefined);
    meetingRef.current = null;
    setParticipants([]);
  }, []);

  useEffect(() => () => { void teardown(true); }, [teardown]);
  useEffect(() => {
    const onHide = () => { if (meetingRef.current) leaveOnUnload(meetingRef.current); };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, []);

  const connect = async (pw?: string) => {
    setBusy(true);
    setError("");
    try {
      const info = await api.join(roomId, pw);
      const room = new LkRoom({
        adaptiveStream: true, dynacast: true,
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        publishDefaults: { screenShareEncoding: { maxBitrate: 4_000_000, maxFramerate: 15 } },
      });
      roomRef.current = room;
      meetingRef.current = info.meeting_id;
      room
        .on(RoomEvent.ParticipantConnected, refresh).on(RoomEvent.ParticipantDisconnected, refresh)
        .on(RoomEvent.TrackMuted, refresh).on(RoomEvent.TrackUnmuted, refresh)
        .on(RoomEvent.LocalTrackPublished, refresh).on(RoomEvent.LocalTrackUnpublished, refresh)
        .on(RoomEvent.TrackPublished, refresh).on(RoomEvent.TrackUnpublished, refresh)
        .on(RoomEvent.TrackSubscribed, (track) => {
          if (track.kind === Track.Kind.Audio && audioBox.current) audioBox.current.appendChild(track.attach());
          refresh();
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => { track.detach().forEach((el) => el.remove()); refresh(); })
        .on(RoomEvent.ActiveSpeakersChanged, refresh)
        .on(RoomEvent.ConnectionStateChanged, setState)
        .on(RoomEvent.Disconnected, () => { setState(ConnectionState.Disconnected); });
      await room.connect(info.livekit_url, info.token);
      await room.startAudio().catch(() => undefined);
      try {
        await room.localParticipant.setMicrophoneEnabled(true);
      } catch {
        setError("Нет доступа к микрофону. Разрешите его в браузере (нужен HTTPS) — вы остаётесь в комнате без звука.");
      }
      setRecording(info.recording);
      setJoin(info);
      setNeedPassword(false);
      refresh();
    } catch (e) {
      await teardown(true); // если встреча уже зарегистрирована, а подключиться к LiveKit не вышло — снимаем себя с участников
      const ae = e as ApiError;
      if (ae.code === "room_password_required" || ae.code === "room_password_invalid") {
        setNeedPassword(true);
        setError(ae.code === "room_password_invalid" ? ae.message : "");
      } else {
        setError(describeConnectError(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const someoneElseSharing = participants.some((p) => p.screen && !p.local);

  const toggle = async (what: "mic" | "cam" | "screen") => {
    const lp = roomRef.current?.localParticipant;
    if (!lp || !join) return;
    setError("");
    try {
      if (what === "mic") await lp.setMicrophoneEnabled(!lp.isMicrophoneEnabled);
      if (what === "cam") await lp.setCameraEnabled(!lp.isCameraEnabled);
      if (what === "screen") {
        if (lp.isScreenShareEnabled) {
          await lp.setScreenShareEnabled(false);
        } else {
          if (join.client.one_sharer_at_a_time && someoneElseSharing) { setError("Сейчас экран уже показывает другой участник."); return; }
          const profile = isScreenProfile(join.client.screen_profile) ? join.client.screen_profile : "sharp";
          const o = screenShareOptions(profile, join.client.screen_share_audio);
          await lp.setScreenShareEnabled(true, o.capture, o.publish);
        }
      }
    } catch (e) {
      const name = (e as Error).name;
      if (name !== "NotAllowedError" && name !== "AbortError") setError("Не удалось переключить устройство — проверьте разрешения браузера.");
    }
    refresh();
  };

  const toggleRecording = async () => {
    if (!join) return;
    try { setRecording((await api.setRecording(join.meeting_id, !recording)).enabled); }
    catch (e) { setError((e as ApiError).message); }
  };

  const onLive = useCallback((e: LiveEvent) => { if (e.type === "recording_changed") setRecording(e.enabled); }, []);
  const leave = async () => { await teardown(true); navigate("/"); };
  const endForAll = async () => {
    if (meetingRef.current) await api.endMeeting(meetingRef.current).catch((e) => setError((e as ApiError).message));
    await teardown(false);
    navigate("/");
  };
  const onMeetingEnded = useCallback(() => { setEnded(true); void teardown(false); }, [teardown]);

  // ------------------------------------------------------------------ вид «до входа»
  if (!join) {
    return (
      <section className="prejoin card">
        <h1>Вход в комнату</h1>
        <p className="muted">Микрофон включится сразу после входа. Реплики участников записываются в протокол встречи.</p>
        {needPassword && (
          <label>Пароль комнаты
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
          </label>
        )}
        {error && <div className="alert error" role="alert">{error}</div>}
        <div className="row">
          <button className="btn primary" disabled={busy || (needPassword && !password)} onClick={() => connect(needPassword ? password : undefined)}>
            {busy ? "Подключение…" : "Войти в комнату"}
          </button>
          <button className="btn ghost" onClick={() => navigate("/")}>Назад</button>
        </div>
      </section>
    );
  }

  const room = join.room;
  const me = participants.find((p) => p.local);
  const sharer = participants.find((p) => p.screen);
  return (
    <div className="room-layout">
      <section className="stage">
        <div className="row">
          <h1>{room.name}</h1>
          <span className="badge">{state === ConnectionState.Connected ? "Подключено" : state === ConnectionState.Reconnecting ? "Переподключение…" : "Нет соединения"}</span>
          {room.transcription_enabled && (
            <span className={`badge ${recording ? "rec" : ""}`}>{recording ? "● Идёт запись" : "Запись остановлена"}</span>
          )}
        </div>
        {ended && <div className="alert">Встреча завершена.</div>}
        {error && <div className="alert error" role="alert">{error}</div>}

        {sharer && <ScreenStage p={sharer} />}

        <div className={`tiles ${sharer ? "strip" : ""}`}>
          {participants.map((p) => (
            <div key={p.identity} className={`tile ${p.speaking ? "speaking" : ""}`}>
              <VideoTile p={p.participant} source={Track.Source.Camera} />
              {!p.cam && <div className="avatar">{p.name.slice(0, 1).toUpperCase()}</div>}
              <div className="tile-foot">
                <span className="tile-name">{p.name}{p.local ? " (вы)" : ""}</span>
                <span title={p.mic ? "Микрофон включён" : "Микрофон выключен"}>{p.mic ? "🎙" : "🔇"}</span>
                {p.cam && <span title="Камера">📷</span>}
                {p.screen && <span title="Показывает экран">🖥</span>}
              </div>
            </div>
          ))}
        </div>

        <div className="controls">
          <button className={`btn ${me?.mic ? "" : "danger"}`} onClick={() => toggle("mic")} disabled={ended}>{me?.mic ? "Выключить микрофон" : "Включить микрофон"}</button>
          {room.camera_allowed && <button className="btn" onClick={() => toggle("cam")} disabled={ended}>{me?.cam ? "Выключить камеру" : "Включить камеру"}</button>}
          {room.screen_share_allowed && (
            <button className={`btn ${me?.screen ? "primary" : "accent"}`} onClick={() => toggle("screen")} disabled={ended}
                    title="Выберите экран, окно или вкладку — трансляция начнётся сразу">
              {me?.screen ? "■ Остановить показ" : "🖥 Показать экран"}
            </button>
          )}
          {room.transcription_enabled && (
            <button className="btn" onClick={toggleRecording} disabled={ended}>{recording ? "Остановить запись" : "Начать запись"}</button>
          )}
          <div className="spacer" />
          {!confirmEnd
            ? <button className="btn ghost" onClick={() => setConfirmEnd(true)} disabled={ended}>Завершить для всех</button>
            : <><span className="muted small">Завершить встречу для всех?</span>
                <button className="btn danger" onClick={endForAll}>Да, завершить</button>
                <button className="btn ghost" onClick={() => setConfirmEnd(false)}>Отмена</button></>}
          <button className="btn danger" onClick={leave}>Выйти</button>
        </div>
        {roomRef.current && <DevicePanel room={roomRef.current} />}
        <div ref={audioBox} className="hidden-audio" aria-hidden />
      </section>
      <TranscriptPanel meetingId={join.meeting_id} enabled={room.transcription_enabled} onMeetingEnded={onMeetingEnded} onEvent={onLive} />
    </div>
  );
}
