import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Track, type Participant } from "livekit-client";
import { Icon } from "../Icons";
import { lastSpeakerOf, pipFocus, videoShowing, type PipCand, type PipFocus } from "../../webPip";

export interface PipPerson extends PipCand { name: string; mic: boolean; participant: Participant }
interface Opts {
  active: boolean; title: string; people: PipPerson[]; micOn: boolean; canMic: boolean; onMic: () => void; onLeave: () => void;
  camOn: boolean; canCam: boolean; onCam: () => void; recording: boolean;
}
type DocPip = { requestWindow: (o: { width: number; height: number }) => Promise<Window>; window: Window | null };
const docPip = (): DocPip | undefined => (window as Window & { documentPictureInPicture?: DocPip }).documentPictureInPicture;

const trackOf = (p: PipPerson | undefined, f: PipFocus | null) =>
  p && f && f.source !== "none" ? p.participant.getTrackPublication(f.source === "screen" ? Track.Source.ScreenShare : Track.Source.Camera)?.track : undefined;

/**
 * Плавающее окно встречи при уходе со вкладки. Только автоматически — отдельной кнопки нет; ручной режим для одной плитки остаётся в её меню.
 * - Chrome и Edge на компьютере: браузер сам вызывает действие Media Session «enterpictureinpicture», когда человек переключается на другую вкладку
 *   страницы с включённым микрофоном или камерой (без нажатия). Открываем окно Document Picture-in-Picture — изображение, время во встрече, участники и кнопки
 *   «Микрофон», «Камера» и «Выйти»;
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

const BOT = /^(EG_|asr-)/;                                  // служебные участники (запись эфира, распознавание) в окне не показываются
const hue = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const tint = (name: string) => `linear-gradient(135deg, hsl(${hue(name)} 62% 52%), hsl(${(hue(name) + 40) % 360} 58% 38%))`;
const initial = (name?: string) => (name ?? "?").trim().slice(0, 1).toUpperCase() || "?";
const clock = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); const p = (n: number) => String(n).padStart(2, "0"); return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`; };

function PipPanel({ person, focus, o }: { person?: PipPerson; focus: PipFocus | null; o: Opts }) {
  const ref = useRef<HTMLVideoElement>(null);
  const track = trackOf(person, focus);
  const t0 = useRef(Date.now());
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((n) => n + 1), 1000); return () => window.clearInterval(t); }, []);
  useEffect(() => {
    const v = ref.current;
    if (!v || !track) return;
    track.attach(v);                                  // через LiveKit: так он знает, что видео в окне видно, и не останавливает поток
    return () => { track.detach(v); };
  }, [track]);
  const all = o.people.filter((p) => !BOT.test(p.identity));
  const crowd = [...all].sort((a, b) => Number(b.speaking) - Number(a.speaking) || Number(a.local) - Number(b.local)).slice(0, 4);
  const { speaking = false, local: self = true } = person ?? {};
  const talking = speaking && !self;
  return (
    <div className="pip">
      {track ? <video ref={ref} className={focus?.source === "screen" ? "fit" : "fill"} autoPlay playsInline muted />
        : <div className={`ava${talking ? " sp" : ""}`} style={{ background: tint(person?.name ?? "?") }}>{initial(person?.name)}</div>}
      <div className="top">
        <div className="ttl">{o.recording && <span className="chip rec">Запись</span>}<b>{o.title}</b></div>
        <div className="meta"><span title="Время во встрече"><Icon name="clock" size={13} />{clock(Date.now() - t0.current)}</span><span className="cnt" title="Участников"><Icon name="users" size={13} />{all.length}</span></div>
      </div>
      {person && (
        <div className={`who${talking ? " talk" : ""}${!person.mic && focus?.source !== "screen" ? " muted" : ""}`}>
          {focus?.source === "screen" ? <Icon name="screen" size={13} /> : <Icon name={person.mic ? "mic" : "micOff"} size={13} />}<span>{whoLabel(person)}</span>
        </div>
      )}
      <div className="bot">
        <div className="crowd" aria-label="Участники">
          {crowd.map((p) => <span key={p.identity} className={`mini${p.speaking ? " sp" : ""}${p.mic ? "" : " mute"}`} style={{ background: tint(p.name) }} title={whoLabel(p)}>{initial(p.name)}</span>)}
          {all.length > crowd.length && <span className="more">+{all.length - crowd.length}</span>}
        </div>
        <div className="btns">
          <button type="button" className={o.micOn ? "on" : "off"} disabled={!o.canMic} aria-pressed={o.micOn} onClick={o.onMic}
                  title={o.micOn ? "Выключить микрофон" : "Включить микрофон"} aria-label="Микрофон"><Icon name={o.micOn ? "mic" : "micOff"} size={18} /></button>
          <button type="button" className={o.camOn ? "on" : "off"} disabled={!o.canCam} aria-pressed={o.camOn} onClick={o.onCam}
                  title={o.camOn ? "Выключить камеру" : "Включить камеру"} aria-label="Камера"><Icon name={o.camOn ? "video" : "videoOff"} size={18} /></button>
          <button type="button" className="leave" onClick={o.onLeave} title="Выйти из встречи"><Icon name="hangup" size={18} /><span>Выйти</span></button>
        </div>
      </div>
    </div>
  );
}

const whoLabel = ({ name, local }: PipPerson) => (local ? `${name} (вы)` : name);

/** Файл стилей окна. Лежит в public/: встроенный <style> закрыт политикой страницы (style-src 'self'), а окно её наследует. При правке файла меняйте его имя (кэш). */
const PIP_CSS = "/pip.v1.css";

/** Окно Document PiP — отдельный документ: свои стили (страницы туда не переходят), язык, заголовок. */
function prepare(w: Window, title: string) {
  const d = w.document;
  d.documentElement.lang = "ru";
  d.title = title;
  d.documentElement.style.background = "#0b1120";             // до загрузки стилей окно не белое (свойство через CSSOM политика разрешает)
  const l = d.createElement("link");
  l.rel = "stylesheet";
  l.href = new URL(PIP_CSS, window.location.href).href;
  d.head.append(l);
}
