import { useCallback, useEffect, useRef, useState } from "react";
import { Participant, Track } from "livekit-client";

export interface PView {
  identity: string; name: string; local: boolean; mic: boolean; cam: boolean; screen: boolean; speaking: boolean; participant: Participant;
}

export function VideoTile({ p, source, className = "video", onSize }: {
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

/** Состояние устройства — текстом и цветом, а не только иконкой: видно издалека и без подсказок. */
function State({ on, onText, offText, kind }: { on: boolean; onText: string; offText: string; kind: "mic" | "cam" }) {
  return <span className={`state ${kind} ${on ? "on" : "off"}`} title={on ? onText : offText}><span aria-hidden>{kind === "mic" ? (on ? "🎙" : "🔇") : on ? "📷" : "🚫"}</span> {on ? onText : offText}</span>;
}

export function ParticipantTile({ p, compact }: { p: PView; compact?: boolean }) {
  const initials = p.name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
  return (
    <div className={`tile ${p.speaking ? "speaking" : ""} ${compact ? "compact" : ""}`} title={p.name}>
      <VideoTile p={p.participant} source={Track.Source.Camera} />
      {!p.cam && <div className="avatar" aria-hidden>{initials}</div>}
      <div className="tile-foot">
        <span className="tile-name">{p.name}{p.local ? " (вы)" : ""}</span>
        <State kind="mic" on={p.mic} onText="микрофон" offText="без звука" />
        {!compact && <State kind="cam" on={p.cam} onText="камера" offText="камера выкл." />}
        {p.screen && <span className="state screen on" title="Показывает экран">🖥 экран</span>}
      </div>
    </div>
  );
}

/** Главный элемент комнаты при показе экрана: максимально большая «сцена», полноэкранный режим, «вписать/заполнить». */
export function ScreenStage({ p }: { p: PView }) {
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
