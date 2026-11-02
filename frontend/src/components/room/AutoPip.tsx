import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Track, type Participant } from "livekit-client";
import { Icon } from "../Icons";
import { lastSpeakerOf, pipFocus, videoShowing, type PipCand, type PipFocus } from "../../webPip";

export interface PipPerson extends PipCand { name: string; mic: boolean; participant: Participant }
interface Opts { active: boolean; title: string; people: PipPerson[]; micOn: boolean; canMic: boolean; onMic: () => void; onLeave: () => void }
type DocPip = { requestWindow: (o: { width: number; height: number }) => Promise<Window>; window: Window | null };
const docPip = (): DocPip | undefined => (window as Window & { documentPictureInPicture?: DocPip }).documentPictureInPicture;

const trackOf = (p: PipPerson | undefined, f: PipFocus | null) =>
  p && f && f.source !== "none" ? p.participant.getTrackPublication(f.source === "screen" ? Track.Source.ScreenShare : Track.Source.Camera)?.track : undefined;

/**
 * Плавающее окно встречи при уходе со вкладки. Только автоматически — отдельной кнопки нет; ручной режим для одной плитки остаётся в её меню.
 * - Chrome и Edge на компьютере: браузер сам вызывает действие Media Session «enterpictureinpicture», когда человек переключается на другую вкладку
 *   страницы с включённым микрофоном или камерой (без нажатия). Открываем окно Document Picture-in-Picture — изображение и кнопки «Микрофон» и «Выйти»;
 *   где его нет — системное окно для видео (только изображение).
 * - Safari (iPhone, iPad, Mac): видео, которое сейчас главное, помечается autopictureinpicture — система переводит его в окно при сворачивании.
 * - Остальные браузеры (Firefox, Chrome на Android, приложение Windows): без автоперехода, страница ведёт себя как раньше.
 * Возврат на вкладку закрывает окно. LiveKit считает видео в окне видимым и продолжает его получать (adaptiveStream).
 */
export function useAutoPip(o: Opts): ReactNode {
  const [win, setWin] = useState<Window | null>(null);
  const spoke = useRef<string | null>(null);
  spoke.current = lastSpeakerOf(o.people, spoke.current);
  const focus = pipFocus(o.people, spoke.current);
  const cur = useRef({ o, focus });
  cur.current = { o, focus };
  const opened = useRef<HTMLVideoElement | null>(null);           // видео, которое мы сами отправили в системное окно

  useEffect(() => {
    if (!o.active) return;
    const close = () => {
      docPip()?.window?.close();
      const d = document as Document & { pictureInPictureElement?: Element | null; exitPictureInPicture?: () => Promise<void> };
      if (opened.current && d.pictureInPictureElement === opened.current) void d.exitPictureInPicture?.().catch(() => undefined);
      opened.current = null;
    };
    const open = async () => {
      const dp = docPip();
      if (dp?.requestWindow && !dp.window) {
        try {
          const w = await dp.requestWindow({ width: 400, height: 250 });
          prepare(w, cur.current.o.title);
          w.addEventListener("pagehide", () => setWin(null), { once: true });
          setWin(w);
          return;
        } catch { /* окно не дали (политика, нет поддержки) — пробуем окно для видео */ }
      }
      const { o: c, focus: f } = cur.current;
      const v = videoShowing(document, trackOf(c.people.find((p) => p.identity === f?.identity), f)?.mediaStreamTrack.id) as (HTMLVideoElement & { requestPictureInPicture?: () => Promise<unknown> }) | null;
      if (v?.requestPictureInPicture && document.pictureInPictureEnabled) { opened.current = v; await v.requestPictureInPicture().catch(() => { opened.current = null; }); }
    };
    const ms = navigator.mediaSession;
    try { ms?.setActionHandler("enterpictureinpicture" as MediaSessionAction, () => void open()); } catch { /* браузер не знает этого действия — автоперехода нет */ }
    const onVis = () => { if (document.visibilityState === "visible") close(); };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      try { ms?.setActionHandler("enterpictureinpicture" as MediaSessionAction, null); } catch { /* см. выше */ }
      document.removeEventListener("visibilitychange", onVis);
      close();
    };
  }, [o.active]);

  // Safari: главное видео уходит в системное окно само при сворачивании (атрибут autopictureinpicture); пометка переходит за выбранным собеседником
  const trackId = trackOf(o.people.find((p) => p.identity === focus?.identity), focus)?.mediaStreamTrack.id;
  useEffect(() => {
    const target = o.active ? videoShowing(document, trackId) : null;
    for (const v of document.querySelectorAll<HTMLVideoElement>("video[autopictureinpicture]")) if (v !== target) { v.removeAttribute("autopictureinpicture"); (v as HTMLVideoElement & { autoPictureInPicture?: boolean }).autoPictureInPicture = false; }
    if (target) { target.setAttribute("autopictureinpicture", ""); (target as HTMLVideoElement & { autoPictureInPicture?: boolean }).autoPictureInPicture = true; }
  });

  if (!win || !o.active) return null;
  const person = o.people.find((p) => p.identity === focus?.identity);
  return createPortal(<PipPanel person={person} focus={focus} o={o} />, win.document.body);
}

function PipPanel({ person, focus, o }: { person?: PipPerson; focus: PipFocus | null; o: Opts }) {
  const ref = useRef<HTMLVideoElement>(null);
  const track = trackOf(person, focus);
  useEffect(() => {
    const v = ref.current;
    if (!v || !track) return;
    track.attach(v);                                  // через LiveKit: так он знает, что видео в окне видно, и не останавливает поток
    return () => { track.detach(v); };
  }, [track]);
  return (
    <div className="pip">
      {track ? <video ref={ref} className={focus?.source === "screen" ? "fit" : "fill"} autoPlay playsInline muted />
        : <div className="ava">{(person?.name ?? "?").trim().slice(0, 1).toUpperCase()}</div>}
      {person && <div className="who">{!person.mic && focus?.source !== "screen" && <Icon name="micOff" size={13} />}<span>{whoLabel(person)}</span></div>}
      <div className="bar">
        <button type="button" className={o.micOn ? "on" : "off"} disabled={!o.canMic} aria-pressed={o.micOn} onClick={o.onMic}
                title={o.micOn ? "Выключить микрофон" : "Включить микрофон"}><Icon name={o.micOn ? "mic" : "micOff"} size={18} /><span>Микрофон</span></button>
        <button type="button" className="leave" onClick={o.onLeave} title="Выйти из встречи"><Icon name="hangup" size={18} /><span>Выйти</span></button>
      </div>
    </div>
  );
}

const whoLabel = ({ name, local }: PipPerson) => (local ? `${name} (вы)` : name);

/** Окно Document PiP — отдельный документ: свои стили (страницы туда не переходят), язык, заголовок. */
function prepare(w: Window, title: string) {
  const d = w.document;
  d.documentElement.lang = "ru";
  d.title = title;
  const st = d.createElement("style");
  st.textContent = `
    :root { color-scheme: dark; }
    html, body { margin: 0; height: 100%; background: #0d1320; color: #e8edf6; font: 13px/1.3 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; overflow: hidden; }
    .pip { position: relative; height: 100%; display: grid; place-items: center; }
    .pip video { position: absolute; inset: 0; width: 100%; height: 100%; background: #000; }
    .pip video.fit { object-fit: contain; } .pip video.fill { object-fit: cover; }
    .ava { width: 64px; height: 64px; border-radius: 50%; display: grid; place-items: center; background: #2b3a5e; font-size: 26px; font-weight: 600; }
    .who { position: absolute; left: 8px; top: 8px; display: flex; gap: 4px; align-items: center; max-width: calc(100% - 16px); padding: 3px 8px; border-radius: 8px; background: rgb(0 0 0 / .6); }
    .who span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .who svg { color: #ff8a80; flex: none; }
    .bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; gap: 8px; justify-content: center; padding: 10px; background: linear-gradient(transparent, rgb(0 0 0 / .7)); }
    .bar button { display: inline-flex; align-items: center; gap: 6px; min-height: 36px; padding: 0 14px; border: 0; border-radius: 18px; font: inherit; font-weight: 600; color: #fff; background: #2a3448; cursor: pointer; }
    .bar button.off { background: #5a2420; } .bar button.leave { background: #cc3535; }
    .bar button:hover:not(:disabled) { filter: brightness(1.15); } .bar button:disabled { opacity: .45; cursor: default; }
    .bar button:focus-visible { outline: 2px solid #8fb2ff; outline-offset: 2px; }
    @media (max-width: 300px) { .bar button span { display: none; } .bar button { padding: 0 10px; } }
  `;
  d.head.append(st);
}
