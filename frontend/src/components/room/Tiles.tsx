import { useCallback, useEffect, useRef, useState } from "react";
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

/** Действия руководителя над участником; нет обработчика — нет и пункта меню. Права всё равно проверяет сервер. */
export interface TileActions {
  presentation: boolean;
  onMute?: (p: PView) => void;
  onFloor?: (p: PView, granted: boolean) => void;
  onKick?: (p: PView) => void;
  /** Опустить руку участнику (руководитель). */
  onLowerHand?: (p: PView) => void;
  onStopCamera?: (p: PView) => void;
  /** Остановить показ экрана; block — и запретить повторный показ до конца встречи. */
  onStopShare?: (p: PView, block: boolean) => void;
  onAllowShare?: (p: PView) => void;
  /** Кому в этой встрече запрещён показ экрана (известно по событиям встречи). */
  shareBlocked?: ReadonlySet<string>;
}

/** Пункты модерации — одни и те же в меню плитки участника и плитки его экрана (единый список, без второго меню). */
export function moderationItems(p: PView, a: TileActions | undefined, only?: "screen"): MenuItem[] {
  if (!a || p.local) return [];
  const blocked = !!a.shareBlocked?.has(p.identity);
  const share: MenuItem[] = [
    { id: "stopshare", label: "Остановить показ экрана", icon: "screenStop", hidden: !(a.onStopShare && p.screen), onSelect: () => a.onStopShare?.(p, false) },
    { id: "blockshare", label: "Остановить и запретить показ", icon: "screenStop", danger: true, hidden: !(a.onStopShare && !p.leader && !blocked && (p.screen || !only)),
      confirm: `Запретить участнику «${p.name}» показывать экран до конца встречи? Снять запрет можно в этом же меню.`, onSelect: () => a.onStopShare?.(p, true) },
    { id: "allowshare", label: "Разрешить показ экрана", icon: "screen", hidden: !(a.onAllowShare && blocked), onSelect: () => a.onAllowShare?.(p) },
  ];
  if (only === "screen") return share;
  return [
    { id: "floor", label: p.floor ? "Забрать слово" : "Дать слово", icon: "hand", hidden: !(a.presentation && a.onFloor && !p.leader), onSelect: () => a.onFloor?.(p, !p.floor) },
    { id: "hand", label: "Снять поднятую руку", icon: "hand", hidden: !(a.onLowerHand && p.hand), onSelect: () => a.onLowerHand?.(p) },
    { id: "mute", label: "Выключить микрофон", icon: "micOff", hidden: !(a.onMute && p.mic), onSelect: () => a.onMute?.(p) },
    { id: "stopcam", label: "Выключить камеру", icon: "videoOff", hidden: !(a.onStopCamera && p.cam), onSelect: () => a.onStopCamera?.(p) },
    ...share,
    { id: "kick", label: "Удалить из встречи", icon: "userx", danger: true, hidden: !(a.onKick && !p.leader), confirm: `Удалить «${p.name}» из встречи?`, onSelect: () => a.onKick?.(p) },
  ];
}

/** Полный экран и «картинка в картинке» для конкретной плитки (видео не переносится — браузер разворачивает тот же элемент). */
export function toggleFullscreen(el: Element | null | undefined) {
  if (!el) return;
  if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
  else void (el as HTMLElement).requestFullscreen?.().catch(() => undefined);
}
export const pipSupported = () => typeof document !== "undefined" && !!(document as Document & { pictureInPictureEnabled?: boolean }).pictureInPictureEnabled;
export function togglePip(video: HTMLVideoElement | null | undefined) {
  if (!video) return;
  const d = document as Document & { pictureInPictureElement?: Element | null; exitPictureInPicture?: () => Promise<void> };
  if (d.pictureInPictureElement === video) void d.exitPictureInPicture?.().catch(() => undefined);
  else void (video as HTMLVideoElement & { requestPictureInPicture?: () => Promise<unknown> }).requestPictureInPicture?.().catch(() => undefined);
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

export function ParticipantTile({ p, compact, actions, onCard, meetingId, avatarUrl, localMuted, onLocalMute, stageItems, onOpen }: {
  p: PView; compact?: boolean; actions?: TileActions; onCard?: (p: PView) => void; meetingId?: string; avatarUrl?: string; localMuted?: boolean; onLocalMute?: (identity: string) => void;
  /** Пункты сцены (крупно, закрепить, показать всем…) — от владельца раскладки. */
  stageItems?: () => MenuItem[];
  /** Двойной клик / Alt+Enter: открыть крупно или вернуть. */
  onOpen?: () => void;
}) {
  const [avFailed, setAvFailed] = useState(false);
  const { onContextMenu, node: ctxNode, openAt } = useContextMenu();
  const [flash, setFlash] = useState("");
  const say = (t: string) => { setFlash(t); window.setTimeout(() => setFlash(""), 2200); };
  // Одно меню на всё: правая кнопка и «⋯» открывают один и тот же список (недоступное этому пользователю не показывается; права проверяет сервер).
  const items = (): MenuItem[] => [
    { id: "card", label: "Открыть карточку", icon: "user", hidden: !onCard || p.local, onSelect: () => onCard?.(p) },
    ...(stageItems?.() ?? []),
    { id: "lmute", label: localMuted ? "Включить звук для себя" : "Заглушить для себя", icon: localMuted ? "mic" : "micOff", hidden: p.local || !onLocalMute, onSelect: () => onLocalMute?.(p.identity) },
    { id: "mention", label: "Упомянуть в чате", icon: "chat", hidden: p.local, onSelect: () => window.dispatchEvent(new CustomEvent("pg:mention", { detail: p.name })) },
    { id: "name", label: "Копировать ФИО", icon: "copy", onSelect: () => void copyText(p.name).then((ok) => say(ok ? "ФИО скопировано" : "Не удалось скопировать")) },
    { id: "mail", label: "Копировать e-mail", icon: "copy", hidden: !meetingId || p.local,
      onSelect: () => void api.participantCard(meetingId!, p.identity).then((c) => (c.email ? copyText(c.email).then((ok) => say(ok ? "E-mail скопирован" : "Не удалось скопировать")) : say("E-mail не указан"))).catch(() => say("Карточка недоступна")) },
    ...moderationItems(p, actions),
  ];
  const initials = p.name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
  const openMenu = (e: React.MouseEvent) => {
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    openAt(r.left, r.bottom + 4, items());
  };
  const manageable = !!actions && !p.local && moderationItems(p, actions).some((i) => !i.hidden);
  return (
    <div onContextMenu={onContextMenu(items)} className={`tile ${p.speaking ? "speaking" : ""} ${compact ? "compact" : ""} ${p.floor ? "has-floor" : ""} ${manageable ? "manageable" : ""}`} title={p.name}
         onClick={onCard ? () => onCard(p) : undefined} onDoubleClick={onOpen ? (e) => { e.stopPropagation(); onOpen(); } : undefined}
         role={onCard ? "button" : undefined} tabIndex={onCard || onOpen ? 0 : undefined}
         onKeyDown={(e) => {
           if (e.target !== e.currentTarget) return;
           if (onOpen && e.key === "Enter" && e.altKey) { e.preventDefault(); onOpen(); }
           else if (onCard && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onCard(p); }
         }}>
      {localMuted && <span className="tile-lmute" title="Вы заглушили этого участника только для себя — остальные его слышат" role="img" aria-label="Заглушён для вас"><Icon name="micOff" size={14} /></span>}
      {p.hand && <span className="tile-hand" title={p.handOrder ? `Поднял руку (в очереди: ${p.handOrder})` : "Поднял руку"} role="img" aria-label="Поднята рука"><Icon name="hand" size={16} />{p.handOrder ? <b>{p.handOrder}</b> : null}</span>}
      <VideoTile p={p.participant} source={Track.Source.Camera} />
      {!p.cam && <div className="avatar" aria-hidden>{avatarUrl && !avFailed ? <img src={avatarUrl} alt="" draggable={false} onError={() => setAvFailed(true)} /> : initials}</div>}
      {(p.floor || p.leader) && (
        <span className={`tile-role ${p.floor ? "floor" : "leader"}`} title={p.floor ? "Участнику дано слово" : "Руководитель комнаты"}>
          {p.floor ? <><Icon name="hand" size={14} /> Слово</> : "Руководитель"}
        </span>
      )}
      <button type="button" className="tile-more" onClick={openMenu} onDoubleClick={(e) => e.stopPropagation()} title={`Действия: ${p.name}`} aria-label={`Действия с участником ${p.name}`} aria-haspopup="menu">
        <Icon name="more" size={18} />
      </button>
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
export function ScreenStage({ p, big = true, items, onOpen }: {
  p: PView;
  /** Крупно (масштаб, панель управления) или в ленте/сетке (только подпись; клик — открыть крупно). Видео при смене не пересоздаётся. */
  big?: boolean;
  /** Пункты меню плитки экрана (правая кнопка и «⋯»). */
  items?: () => MenuItem[];
  onOpen?: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const { onContextMenu, node: ctxNode, openAt } = useContextMenu();
  const bigRef = useRef(big);
  bigRef.current = big;
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
  useEffect(() => { if (!big) { setView(IDENTITY); setFill(false); } }, [big]);         // в ленте — всегда исходный вид

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
      if (!bigRef.current || (e.target as HTMLElement).closest(".screen-bar, .zoom-badge")) return;     // в ленте колесо прокручивает ленту
      e.preventDefault();   // колесо над экраном — приближение, а не прокрутка страницы
      zoomBy(wheelFactor(e.deltaY, e.deltaMode, e.ctrlKey), local(e));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomBy, local]);

  const onPointerDown = (e: React.PointerEvent) => {
    if (!big || (e.target as HTMLElement).closest(".screen-bar, .zoom-badge") || (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1)) return;
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
    if (!big) { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget && onOpen) { e.preventDefault(); onOpen(); } return; }
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

  const fullscreen = () => toggleFullscreen(box.current);
  const openMenu = (e: React.MouseEvent) => { e.stopPropagation(); const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); if (items) openAt(r.left, r.bottom + 4, items()); };
  const title = p.local ? "Вы показываете экран" : `Экран: ${p.name}`;
  const zoomed = view.s > 1;
  const zoomStyle = zoomed ? { width: stage.w * view.s, height: stage.h * view.s, transform: `translate(${view.x}px, ${view.y}px)` } : undefined;
  return (
    <div className={`screen-stage ${big ? "big" : "small"} ${fill ? "fill" : ""} ${zoomed ? "zoomed" : ""} ${panning ? "panning" : ""}`} ref={box} tabIndex={0} data-identity={p.identity}
         onContextMenu={items ? onContextMenu(items) : undefined}
         onClick={!big && onOpen ? () => onOpen() : undefined}
         onDoubleClick={(e) => { if (big && !(e.target as HTMLElement).closest(".screen-bar, .zoom-badge, .tile-more")) fullscreen(); }}
         onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onKeyDown={onKeyDown}
         role={big ? undefined : "button"}
         aria-label={big ? `${title}. Колесо мыши — масштаб, перетаскивание — сдвиг, клавиши плюс, минус и ноль — масштаб` : `${title}. Enter — открыть крупно`}>
      <div className="screen-zoom" style={zoomStyle}>
        <VideoTile p={p.participant} source={Track.Source.ScreenShare} className="screen-video" onSize={onSize} />
      </div>
      {big && zoomed && (
        <div className="zoom-badge" role="group" aria-label="Масштаб">
          <button className="btn mini" onClick={() => zoomBy(0.8)} title="Отдалить (−)" aria-label="Отдалить">−</button>
          <button className="btn mini" onClick={() => setView(IDENTITY)} title="Вернуть исходный вид (0)">{percent(view)} · сбросить</button>
          <button className="btn mini" onClick={() => zoomBy(1.25)} title="Приблизить (+)" aria-label="Приблизить">+</button>
        </div>
      )}
      {big ? (
        <div className="screen-bar">
          <span>{title}{size && <span className="muted"> · {size}</span>}
            <span className="muted hint-zoom"> · колесо мыши — приблизить, перетаскивание — сдвиг</span></span>
          <span className="spacer" />
          {!zoomed && <button className="btn mini" onClick={() => zoomBy(1.25)} title="Приблизить (+)" aria-label="Приблизить">+</button>}
          <button className="btn mini" onClick={() => setFill((f) => !f)} title="Вписать / заполнить окно">{fill ? "Вписать" : "Заполнить"}</button>
          <button className="btn mini" onClick={fullscreen} title="Полный экран (или двойной клик)">⛶ На весь экран</button>
        </div>
      ) : <div className="screen-label"><Icon name="screen" size={14} /> {p.local ? "Ваш экран" : p.name}</div>}
      {items && (
        <button type="button" className="tile-more" onClick={openMenu} onDoubleClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}
                title={`Действия: ${title}`} aria-label={`Действия: ${title}`} aria-haspopup="menu"><Icon name="more" size={18} /></button>
      )}
      {ctxNode}
    </div>
  );
}
