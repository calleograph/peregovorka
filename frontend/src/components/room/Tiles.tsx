import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Participant, Track } from "livekit-client";
import { Icon } from "../Icons";
import { useContextMenu, type MenuItem } from "../ContextMenu";
import { api } from "../../api";
import { copyText } from "../../util";
import { IDENTITY, clampView, panBy, percent, wheelFactor, zoomAt, type Size, type View } from "../../screenZoom";

export interface PView {
  identity: string; name: string; local: boolean; mic: boolean; cam: boolean; screen: boolean; speaking: boolean; participant: Participant;
  /** Участнику дано слово (презентационная комната) / участник — руководитель комнаты. */
  floor?: boolean; leader?: boolean;
  /** Поднята рука; номер в очереди (1 — раньше всех). */
  hand?: boolean; handOrder?: number;
}

/** Действия руководителя над участником; нет обработчика — нет и пункта меню. */
export interface TileActions {
  presentation: boolean;
  onMute?: (p: PView) => void;
  onFloor?: (p: PView, granted: boolean) => void;
  onKick?: (p: PView) => void;
  /** Опустить руку участнику (руководитель). */
  onLowerHand?: (p: PView) => void;
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
/** Индикатор микрофона: контейнер фиксированного размера (28×28), внутри меняется только состояние — размер строки при речи не меняется. */
function MicIndicator({ on, speaking, name }: { on: boolean; speaking: boolean; name: string }) {
  const text = on ? (speaking ? "говорит" : "микрофон включён") : "без звука";
  return (
    <span className={`mic-ind ${on ? "on" : "off"} ${on && speaking ? "speaking" : ""}`} role="img" aria-label={`${name}: ${text}`} title={text}>
      <i className="mic-ring" aria-hidden />
      <Icon name={on ? "mic" : "micOff"} size={15} />
    </span>
  );
}

function State({ on, onText, offText, kind }: { on: boolean; onText: string; offText: string; kind: "mic" | "cam" }) {
  return <span className={`state ${kind} ${on ? "on" : "off"}`} title={on ? onText : offText}><span aria-hidden>{kind === "mic" ? (on ? "🎙" : "🔇") : on ? "📷" : "🚫"}</span> {on ? onText : offText}</span>;
}

/** Меню действий над участником (⋯ на плитке или клик по плитке): дать/забрать слово, выключить микрофон, удалить из встречи. */
function TileMenu({ p, actions, pos, onClose }: { p: PView; actions: TileActions; pos: { top: number; right: number }; onClose: () => void }) {
  const [confirmKick, setConfirmKick] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [onClose]);
  const run = (fn: () => void) => () => { fn(); onClose(); };
  return createPortal(
    <div className="tile-menu" role="menu" ref={ref} style={{ top: pos.top, right: pos.right }} onClick={(e) => e.stopPropagation()}>
      <div className="tile-menu-head" title={p.name}>{p.name}</div>
      {actions.presentation && actions.onFloor && !p.leader && (
        p.floor
          ? <button type="button" role="menuitem" onClick={run(() => actions.onFloor!(p, false))}><Icon name="hand" size={16} /> Забрать слово</button>
          : <button type="button" role="menuitem" onClick={run(() => actions.onFloor!(p, true))}><Icon name="hand" size={16} /> Дать слово</button>
      )}
      {actions.onLowerHand && p.hand && <button type="button" role="menuitem" onClick={run(() => actions.onLowerHand!(p))}><Icon name="hand" size={16} /> Опустить руку</button>}
      {actions.onMute && <button type="button" role="menuitem" disabled={!p.mic} onClick={run(() => actions.onMute!(p))}><Icon name="micOff" size={16} /> {p.mic ? "Выключить микрофон" : "Микрофон уже выключен"}</button>}
      {actions.onKick && !p.leader && (confirmKick
        ? <button type="button" role="menuitem" className="danger" onClick={run(() => actions.onKick!(p))}><Icon name="userx" size={16} /> Точно удалить?</button>
        : <button type="button" role="menuitem" className="danger" onClick={() => setConfirmKick(true)}><Icon name="userx" size={16} /> Удалить из встречи</button>)}
    </div>,
    document.body,
  );
}

export function ParticipantTile({ p, compact, actions, onCard, meetingId, avatarUrl, localMuted, onLocalMute }: { p: PView; compact?: boolean; actions?: TileActions; onCard?: (p: PView) => void; meetingId?: string; avatarUrl?: string; localMuted?: boolean; onLocalMute?: (identity: string) => void }) {
  const [avFailed, setAvFailed] = useState(false);
  const { onContextMenu, node: ctxNode } = useContextMenu();
  const [flash, setFlash] = useState("");
  const say = (t: string) => { setFlash(t); window.setTimeout(() => setFlash(""), 2200); };
  // Правая кнопка: быстрые действия над участником (каждое доступно и обычным путём: клик по плитке, «⋯», карточка). Недоступное данному пользователю не показывается.
  const items = (): MenuItem[] => [
    { id: "card", label: "Открыть карточку", icon: "user", hidden: !onCard || p.local, onSelect: () => onCard?.(p) },
    { id: "lmute", label: localMuted ? "Включить звук" : "Заглушить для себя", icon: localMuted ? "mic" : "micOff", hidden: p.local || !onLocalMute, onSelect: () => onLocalMute?.(p.identity) },
    { id: "mention", label: "Упомянуть в чате", icon: "chat", hidden: p.local, onSelect: () => window.dispatchEvent(new CustomEvent("pg:mention", { detail: p.name })) },
    { id: "name", label: "Копировать ФИО", icon: "copy", onSelect: () => void copyText(p.name).then((ok) => say(ok ? "ФИО скопировано" : "Не удалось скопировать")) },
    { id: "mail", label: "Копировать e-mail", icon: "copy", hidden: !meetingId || p.local,
      onSelect: () => void api.participantCard(meetingId!, p.identity).then((c) => (c.email ? copyText(c.email).then((ok) => say(ok ? "E-mail скопирован" : "Не удалось скопировать")) : say("E-mail не указан"))).catch(() => say("Карточка недоступна")) },
    { id: "floor", label: p.floor ? "Забрать слово" : "Дать слово", icon: "hand", hidden: !(actions?.presentation && actions.onFloor && !p.leader && !p.local), onSelect: () => actions?.onFloor?.(p, !p.floor) },
    { id: "hand", label: "Снять поднятую руку", icon: "hand", hidden: !(actions?.onLowerHand && p.hand && !p.local), onSelect: () => actions?.onLowerHand?.(p) },
    { id: "mute", label: "Выключить микрофон", icon: "micOff", hidden: !(actions?.onMute && p.mic && !p.local), onSelect: () => actions?.onMute?.(p) },
    { id: "kick", label: "Удалить из встречи", icon: "userx", danger: true, hidden: !(actions?.onKick && !p.leader && !p.local), confirm: `Удалить «${p.name}» из встречи?`, onSelect: () => actions?.onKick?.(p) },
  ];
  const initials = p.name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
  const [menu, setMenu] = useState<{ top: number; right: number } | null>(null);
  const tileRef = useRef<HTMLDivElement>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  // меню выводится поверх страницы (плитка обрезает содержимое), у правого верхнего угла плитки
  const toggleMenu = () => setMenu((m) => {
    if (m) return null;
    const r = tileRef.current?.getBoundingClientRect();
    if (!r) return null;
    return { top: Math.max(8, Math.min(r.top + 40, window.innerHeight - 170)), right: Math.max(8, window.innerWidth - r.right + 8) };
  });
  const manageable = !!actions && !p.local && !!(actions.onMute || actions.onKick || actions.onLowerHand || (actions.presentation && actions.onFloor));
  return (
    <div ref={tileRef} onContextMenu={onContextMenu(items)} className={`tile ${p.speaking ? "speaking" : ""} ${compact ? "compact" : ""} ${p.floor ? "has-floor" : ""} ${manageable ? "manageable" : ""}`} title={p.name}
         onClick={onCard ? () => onCard(p) : manageable ? toggleMenu : undefined} role={onCard ? "button" : undefined} tabIndex={onCard ? 0 : undefined}
         onKeyDown={onCard ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onCard(p); } } : undefined}>
      {localMuted && <span className="tile-lmute" title="Вы заглушили этого участника только для себя — остальные его слышат" role="img" aria-label="Заглушён для вас"><Icon name="micOff" size={14} /></span>}
      {p.hand && <span className="tile-hand" title={p.handOrder ? `Поднял руку (в очереди: ${p.handOrder})` : "Поднял руку"} role="img" aria-label="Поднята рука"><Icon name="hand" size={16} />{p.handOrder ? <b>{p.handOrder}</b> : null}</span>}
      <VideoTile p={p.participant} source={Track.Source.Camera} />
      {!p.cam && <div className="avatar" aria-hidden>{avatarUrl && !avFailed ? <img src={avatarUrl} alt="" draggable={false} onError={() => setAvFailed(true)} /> : initials}</div>}
      {(p.floor || p.leader) && (
        <span className={`tile-role ${p.floor ? "floor" : "leader"}`} title={p.floor ? "Участнику дано слово" : "Руководитель комнаты"}>
          {p.floor ? <><Icon name="hand" size={14} /> Слово</> : "Руководитель"}
        </span>
      )}
      {manageable && actions && (
        <>
          <button type="button" className="tile-more" onClick={(e) => { e.stopPropagation(); toggleMenu(); }} title={`Действия: ${p.name}`} aria-label={`Действия с участником ${p.name}`} aria-haspopup="menu" aria-expanded={!!menu}>
            <Icon name="more" size={18} />
          </button>
          {menu && <TileMenu p={p} actions={actions} pos={menu} onClose={closeMenu} />}
        </>
      )}
      {ctxNode}
      {flash && <span className="tile-flash" role="status">{flash}</span>}
      <div className="tile-foot">
        <span className="tile-name">{p.name}{p.local ? " (вы)" : ""}</span>
        <MicIndicator on={p.mic} speaking={p.speaking} name={p.name} />
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
