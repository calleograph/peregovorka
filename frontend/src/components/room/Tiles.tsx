import { useCallback, useEffect, useRef, useState } from "react";
import { Participant, Track } from "livekit-client";
import { IDENTITY, clampView, panBy, percent, wheelFactor, zoomAt, type Size, type View } from "../../screenZoom";

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

/**
 * Главный элемент комнаты при показе экрана: максимально большая «сцена», полноэкранный режим, «вписать/заполнить» и масштабирование
 * как в просмотрщиках изображений: колесо мыши — приблизить/отдалить к точке под курсором, перетаскивание — сдвиг, «щипок» на сенсорном
 * экране, клавиши + − 0 и стрелки. Отдалить меньше исходного вида нельзя, приблизить — до предела из screenZoom.ts.
 */
export function ScreenStage({ p }: { p: PView }) {
  const box = useRef<HTMLDivElement>(null);
  const [vsize, setVsize] = useState<Size>({ w: 0, h: 0 });
  const [stage, setStage] = useState<Size>({ w: 0, h: 0 });
  const [fill, setFill] = useState(false);
  const [view, setView] = useState<View>(IDENTITY);
  const [panning, setPanning] = useState(false);
  const onSize = useCallback((w: number, h: number) => setVsize((o) => (o.w === w && o.h === h ? o : { w, h })), []);
  const size = vsize.w && vsize.h ? `${vsize.w}×${vsize.h}` : "";
  // актуальные значения для обработчиков событий (колесо вешается один раз, как не-passive слушатель)
  const geo = useRef({ view, stage, vsize, fill });
  geo.current = { view, stage, vsize, fill };
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef(0);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setStage({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // изменился размер сцены/кадра или режим — допустимый сдвиг пересчитывается
  useEffect(() => { setView((v) => clampView(v, stage, vsize, fill)); }, [stage, vsize, fill]);

  const local = useCallback((e: { clientX: number; clientY: number }) => {
    const r = box.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }, []);
  const zoomBy = useCallback((factor: number, at?: { x: number; y: number }) => {
    const g = geo.current;
    const c = at ?? { x: g.stage.w / 2, y: g.stage.h / 2 };
    setView(zoomAt(g.view, factor, c.x, c.y, g.stage, g.vsize, g.fill));
  }, []);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if ((e.target as HTMLElement).closest(".screen-bar, .zoom-badge")) return;
      e.preventDefault();   // колесо над экраном — приближение, а не прокрутка страницы
      zoomBy(wheelFactor(e.deltaY, e.deltaMode, e.ctrlKey), local(e));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomBy, local]);

  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest(".screen-bar, .zoom-badge") || (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1)) return;
    pointers.current.set(e.pointerId, local(e));
    box.current?.setPointerCapture(e.pointerId);
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = Math.hypot(a.x - b.x, a.y - b.y);
    }
    if (geo.current.view.s > 1) setPanning(true);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    const cur = local(e);
    pointers.current.set(e.pointerId, cur);
    const g = geo.current;
    if (pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch.current > 0 && dist > 0) zoomBy(dist / pinch.current, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
      pinch.current = dist;
    } else if (g.view.s > 1) {
      setView(panBy(g.view, cur.x - prev.x, cur.y - prev.y, g.stage, g.vsize, g.fill));
    }
  };
  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    pinch.current = 0;
    if (pointers.current.size === 0) setPanning(false);
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    const g = geo.current, step = 60;
    const pan = (dx: number, dy: number) => setView(panBy(g.view, dx, dy, g.stage, g.vsize, g.fill));
    if (e.key === "+" || e.key === "=") zoomBy(1.25);
    else if (e.key === "-" || e.key === "_") zoomBy(0.8);
    else if (e.key === "0") setView(IDENTITY);
    else if (e.key === "ArrowLeft" && g.view.s > 1) pan(step, 0);
    else if (e.key === "ArrowRight" && g.view.s > 1) pan(-step, 0);
    else if (e.key === "ArrowUp" && g.view.s > 1) pan(0, step);
    else if (e.key === "ArrowDown" && g.view.s > 1) pan(0, -step);
    else return;
    e.preventDefault();
  };

  const fullscreen = () => { const el = box.current; if (!el) return; if (document.fullscreenElement) void document.exitFullscreen(); else void el.requestFullscreen?.(); };
  const zoomed = view.s > 1;
  const zoomStyle = zoomed ? { width: stage.w * view.s, height: stage.h * view.s, transform: `translate(${view.x}px, ${view.y}px)` } : undefined;
  return (
    <div className={`screen-stage ${fill ? "fill" : ""} ${zoomed ? "zoomed" : ""} ${panning ? "panning" : ""}`} ref={box} tabIndex={0}
         onDoubleClick={(e) => { if (!(e.target as HTMLElement).closest(".screen-bar, .zoom-badge")) fullscreen(); }}
         onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onKeyDown={onKeyDown}
         aria-label="Показ экрана. Колесо мыши — масштаб, перетаскивание — сдвиг, клавиши плюс, минус и ноль — масштаб">
      <div className="screen-zoom" style={zoomStyle}>
        <VideoTile p={p.participant} source={Track.Source.ScreenShare} className="screen-video" onSize={onSize} />
      </div>
      {zoomed && (
        <div className="zoom-badge" role="group" aria-label="Масштаб">
          <button className="btn mini" onClick={() => zoomBy(0.8)} title="Отдалить (−)" aria-label="Отдалить">−</button>
          <button className="btn mini" onClick={() => setView(IDENTITY)} title="Вернуть исходный вид (0)">{percent(view)} · сбросить</button>
          <button className="btn mini" onClick={() => zoomBy(1.25)} title="Приблизить (+)" aria-label="Приблизить">+</button>
        </div>
      )}
      <div className="screen-bar">
        <span>{p.local ? "Вы показываете экран" : `Экран: ${p.name}`}{size && <span className="muted"> · {size}</span>}
          <span className="muted hint-zoom"> · колесо мыши — приблизить, перетаскивание — сдвиг</span></span>
        <span className="spacer" />
        {!zoomed && <button className="btn mini" onClick={() => zoomBy(1.25)} title="Приблизить (+)" aria-label="Приблизить">+</button>}
        <button className="btn mini" onClick={() => setFill((f) => !f)} title="Вписать / заполнить окно">{fill ? "Вписать" : "Заполнить"}</button>
        <button className="btn mini" onClick={fullscreen} title="Полный экран (или двойной клик)">⛶ На весь экран</button>
      </div>
    </div>
  );
}
