import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Room, RoomEvent, Track, type Participant, type RemoteTrack } from "livekit-client";
import DrawioFrame, { DRAWIO_SRC_BARE, type FrameHandle } from "../board/DrawioFrame";
import { BoardSync } from "../board/whiteboardSync";
import { pickScene, type StageItem } from "./scene";
import "./recorder.css";

/**
 * Страница-рекордер: её открывает служба Egress в своём браузере (параметры url, token, layout — от Egress; rt — подписанный токен встречи от приложения).
 * Входит в комнату как наблюдатель, воспроизводит звук (Egress пишет звук страницы) и рисует ОБЩУЮ сцену встречи: показ экрана, доску (схему, правки приходят по мере
 * появления), явную сцену ведущего или обычную конференцию. Никакой пользовательской части приложения здесь нет — ни меню, ни настроек, ни личных закреплений.
 * Протокол Egress: console.log("START_RECORDING") — можно писать; console.log("END_RECORDING") — закончить.
 */
interface SceneState { ended: boolean; stage: { items: StageItem[]; at: number | null }; board: { active: boolean; seq: number; xml: string | null; patches: Array<{ seq: number; patch: unknown; checksum: string | null; from?: string; by?: string }> } }

const POLL_MS = 700;

function name(p: Participant): string { return p.name || p.identity; }

export default function RecorderTemplate() {
  const q = useMemo(() => new URLSearchParams(window.location.search), []);
  const rt = q.get("rt") ?? "";
  const room = useMemo(() => new Room({ adaptiveStream: false, dynacast: false }), []);
  const [, force] = useState(0);
  const [scene, setScene] = useState<SceneState | null>(null);
  const [connected, setConnected] = useState(false);
  const bump = () => force((n) => n + 1);
  const started = useRef(false);
  const frame = useRef<FrameHandle>(null);
  const syncRef = useRef<BoardSync | null>(null);
  const lastSeq = useRef(0);
  const stateRef = useRef<SceneState | null>(null);
  const audio = useRef(new Map<string, HTMLMediaElement>());
  const slot = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  // --- комната: подключение, звук, события
  useEffect(() => {
    document.title = "Запись встречи";
    const url = q.get("url") ?? "", token = q.get("token") ?? "";
    const onSub = (t: RemoteTrack) => { if (t.kind === Track.Kind.Audio) { const el = t.attach() as HTMLMediaElement; el.autoplay = true; document.body.appendChild(el); audio.current.set(t.sid ?? "", el); } bump(); };
    const onUnsub = (t: RemoteTrack) => { t.detach().forEach((el) => el.remove()); audio.current.delete(t.sid ?? ""); bump(); };
    room.on(RoomEvent.TrackSubscribed, onSub).on(RoomEvent.TrackUnsubscribed, onUnsub).on(RoomEvent.ParticipantConnected, bump).on(RoomEvent.ParticipantDisconnected, bump)
        .on(RoomEvent.TrackMuted, bump).on(RoomEvent.TrackUnmuted, bump).on(RoomEvent.TrackPublished, bump).on(RoomEvent.TrackUnpublished, bump)
        .on(RoomEvent.ActiveSpeakersChanged, bump)
        .on(RoomEvent.Disconnected, () => { console.log("END_RECORDING"); });
    room.connect(url, token, { autoSubscribe: true }).then(() => setConnected(true)).catch((e) => { console.error("rec: нет подключения к комнате", String(e)); });
    return () => { void room.disconnect(); };
  }, [room, q]);

  // --- сцена: опрос сервера (устойчив к обрывам: пропущенный опрос — следующий приносит то же состояние)
  useEffect(() => {
    let alive = true; let timer = 0;
    const tick = async () => {
      try {
        const r = await fetch(`/api/v1/rec/state?rt=${encodeURIComponent(rt)}`, { cache: "no-store" });
        if (r.ok) {
          const s = (await r.json()) as SceneState;
          if (!alive) return;
          stateRef.current = s; setScene(s);
          const sync = syncRef.current;
          if (sync) for (const p of s.board.patches) if (p.seq > lastSeq.current) { lastSeq.current = p.seq; sync.handleLive({ type: "whiteboard_patch", seq: p.seq, patch: p.patch, checksum: p.checksum, from: p.from, by: p.by }); }
          if (s.ended) console.log("END_RECORDING");
        }
      } catch { /* сеть моргнула: следующий опрос */ }
      if (alive) timer = window.setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [rt]);

  // --- схема: тот же механизм синхронизации, что у участников, но только чтение
  useEffect(() => {
    const sync = new BoardSync({
      clientId: `rec-${Math.random().toString(36).slice(2, 8)}`, readOnly: true, prefetch: false,
      fetchState: async () => { const b = stateRef.current?.board; lastSeq.current = Math.max(lastSeq.current, b?.seq ?? 0); return { xml: b?.xml ?? null, seq: b?.seq ?? 0, patches: (b?.patches ?? []) as never, active: true, can_edit: false, used: !!b?.xml, shapes: 0, updated_at: null, updated_by: null } as never; },
      sendPatch: async () => ({ seq: 0 }), saveSnapshot: async () => ({ saved: false, seq: 0 }), post: (m) => frame.current?.post(m),
    });
    syncRef.current = sync;
    return () => { sync.dispose(); syncRef.current = null; };
  }, []);

  // --- кто что показывает
  const remote = [...room.remoteParticipants.values()].filter((p) => !p.identity.startsWith("EG_") && !p.identity.toLowerCase().includes("recorder"));
  const camOf = (p: Participant) => p.getTrackPublication(Track.Source.Camera);
  const scrOf = (p: Participant) => p.getTrackPublication(Track.Source.ScreenShare);
  const cameras = remote.filter((p) => { const c = camOf(p); return !!c && !c.isMuted && !!c.track; }).map((p) => p.identity);
  const screens = remote.filter((p) => { const c = scrOf(p); return !!c && !!c.track; }).map((p) => p.identity);
  const everyone = [...new Set([...cameras, ...remote.map((p) => p.identity)])];            // без камеры участник представлен плиткой с именем
  const speaker = room.activeSpeakers.find((p) => !p.isLocal)?.identity ?? null;
  const sc = pickScene({ stage: scene?.stage.items ?? [], boardActive: !!scene?.board.active, boardHasContent: !!scene?.board.xml, cameras: everyone, screens, speaker });
  const byId = new Map(remote.map((p) => [p.identity, p] as const));

  useEffect(() => {          // можно писать: подключены и получили первое состояние сцены
    if (connected && scene && !started.current) { started.current = true; console.log("START_RECORDING"); }
  }, [connected, scene]);

  const tile = (key: string, big: boolean) => {
    if (key === "board") return <div key={key} ref={slot} className={`rec-tile board-slot ${big ? "big" : ""}`} />;          // сама доска — в слое ниже: iframe не пересоздаётся при переключениях сцены
    const [kind, id] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
    const p = byId.get(id);
    if (!p) return null;
    const pub = kind === "screen" ? scrOf(p) : camOf(p);
    return <Tile key={key} label={kind === "screen" ? `${name(p)} — экран` : name(p)} track={pub?.track} muted={!pub || !!pub.isMuted || !pub.track} big={big} fit={kind === "screen" ? "contain" : "cover"} />;
  };

  // доска остаётся в дереве всегда (iframe не пересоздаётся), на сцене слой ложится ровно на своё место в сетке
  const boardOn = sc.main.includes("board");
  useLayoutEffect(() => {
    const place = () => { const r = slot.current?.getBoundingClientRect(); setRect(r ? { left: r.left, top: r.top, width: r.width, height: r.height } : null); };
    place(); window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [boardOn, sc.main.length, sc.strip.length]);
  return (
    <div className="rec-root" data-scene={sc.reason}>
      <div className={`rec-main n${Math.max(1, sc.main.length)}`}>
        {sc.main.length === 0 && <div className="rec-empty">Ожидание участников…</div>}
        {sc.main.map((k) => tile(k, true))}
      </div>
      {sc.strip.length > 0 && <div className="rec-strip">{sc.strip.slice(0, 6).map((k) => tile(k, false))}</div>}
      <div className={boardOn && rect ? "rec-board-layer" : "rec-hidden"} aria-hidden={!boardOn} style={boardOn && rect ? rect : undefined}>
        <DrawioFrame ref={frame} src={DRAWIO_SRC_BARE} className="rec-board" title="Общая доска" onMessage={(m) => syncRef.current?.handleFrame(m)} />
      </div>
    </div>
  );
}

function Tile({ label, track, muted, big, fit }: { label: string; track?: RemoteTrack | undefined | { attach(el: HTMLMediaElement): HTMLMediaElement; detach(el: HTMLMediaElement): HTMLMediaElement }; muted: boolean; big: boolean; fit: "cover" | "contain" }) {
  const v = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const el = v.current; const t = track as { attach(el: HTMLMediaElement): unknown; detach(el: HTMLMediaElement): unknown } | undefined;
    if (!el || !t || muted) return;
    t.attach(el);
    return () => { t.detach(el); };
  }, [track, muted]);
  return (
    <div className={`rec-tile ${big ? "big" : ""}`}>
      {muted ? <div className="rec-avatar">{label.slice(0, 2).toUpperCase()}</div> : <video ref={v} autoPlay playsInline muted style={{ objectFit: fit }} />}
      <span className="rec-name">{label}</span>
    </div>
  );
}
