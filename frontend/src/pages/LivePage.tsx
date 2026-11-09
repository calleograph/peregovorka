import { type FormEvent, lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type ApiError, type LiveAccess, type LiveQuestion, type LiveViewerInfo } from "../api";
import { Icon } from "../components/Icons";
import { type Contact, GuestContactFields, contactBody, emailOk, loadContact, nameOk, saveContact } from "../components/GuestContact";
import { initial as adaptiveInitial, step as adaptiveStep, audioNote, lowNote, upNote, type Mode, type Sample, type State as AdaptiveState } from "../live/adaptive";
import { inDesktopClient } from "../clientInfo";
import { ViewerStats } from "../livediag/viewerStats";
import "../live.css";

const LiveDiagPanel = lazy(() => import("../livediag/LiveDiagPanel"));

/**
 * Страница зрителя прямого эфира /live/<токен>: без входа в систему, без подключения к LiveKit. Зритель получает от приложения только короткую подписанную ссылку
 * на плейлист HLS (при включённом пароле — после его проверки), а видео отдаёт nginx напрямую. Подпись продлевается незаметно: плеер подменяет в адресах запросов
 * сегментов только подписанную часть пути, не перезапуская воспроизведение.
 */
const SEEN_KEY = (t: string) => `pg:live:${t}`;
const VIEWER_KEY = (t: string) => `pg:live:viewer:${t}`;
const ssGet = (k: string) => { try { return sessionStorage.getItem(k) ?? ""; } catch { return ""; } };
const ssSet = (k: string, v: string) => { try { if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k); } catch { /* только на эту вкладку */ } };
const HLS_PREFIX = /^(?:https?:\/\/[^/]+)?\/(?:ll)?hls\/[0-9a-f]{24}\/\d+\/[A-Za-z0-9_-]{22}\//;       // адрес с узлом (CDN) или без него

const isLl = (path: string) => path.includes("/llhls/");

/** Выбор качества зрителем (как на YouTube): «Авто» подбирает ступень по связи, остальное — вручную. Выбор запоминается в браузере. */
type Choice = "auto" | "video" | "low" | "audio";
const QKEY = "pg:live:quality";
const loadChoice = (): Choice => { try { const v = localStorage.getItem(QKEY); return v === "video" || v === "low" || v === "audio" ? v : "auto"; } catch { return "auto"; } };
const saveChoice = (c: Choice) => { try { localStorage.setItem(QKEY, c); } catch { /* выбор не запомнится */ } };
interface Switch { to: Mode; from: Mode; at: number; t0: number; base?: number; wait?: boolean }       // идёт переключение качества: ждём, пока новый поток пойдёт
const MODE_NAME: Record<Mode, string> = { video: "обычное качество", low: "низкое качество", audio: "только звук" };
const SWITCH_TEXT: Record<Mode, string> = { video: "Включаем видео", low: "Включаем низкое качество", audio: "Переключаем на звук" };
const QUALITY: { id: Choice; name: string; sub: string }[] = [
  { id: "auto", name: "Авто", sub: "Подбирается по вашей связи" },
  { id: "video", name: "Высокое", sub: "Лучшая картинка, нужен хороший интернет" },
  { id: "low", name: "Низкое (360p)", sub: "Для слабой связи, звук без остановок" },
  { id: "audio", name: "Только звук", sub: "Меньше всего трафика" },
];

/** Нативный HLS — в Safari и на iPhone/iPad (там он надёжнее и экономит заряд; на iOS все браузеры — WebKit). Остальным (в том числе новому Chrome, который тоже отвечает «maybe») — hls.js:
 *  одинаковое поведение, продление подписи без перезапуска. Нативный путь остаётся запасным, если у браузера нет MediaSource. */
const native = () => {
  const ua = navigator.userAgent;
  if (!document.createElement("video").canPlayType("application/vnd.apple.mpegurl")) return false;
  const ios = /iP(hone|ad|od)/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const safari = /Safari\//.test(ua) && !/Chrome|Chromium|Edg|OPR|Android|CriOS|FxiOS/.test(ua);
  const mse = typeof (window as unknown as { MediaSource?: unknown }).MediaSource !== "undefined" || typeof (window as unknown as { ManagedMediaSource?: unknown }).ManagedMediaSource !== "undefined";
  return ios || safari || !mse;
};
const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000)), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return d > 0 ? `${d} д ${two(h)}:${two(m)}:${two(ss)}` : `${two(h)}:${two(m)}:${two(ss)}`;
};
const until = (iso: string) => new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });      // «до 2 ноября в 21:24» — без дня недели в им. падеже
const when = (iso: string) => new Date(iso).toLocaleString("ru-RU", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

/** Сообщение «пароль»: компактная форма с автофокусом. */
function PasswordForm({ onSubmit, busy, error }: { onSubmit: (pw: string) => void; busy: boolean; error: string }) {
  const [pw, setPw] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  const submit = (e: FormEvent) => { e.preventDefault(); if (pw) onSubmit(pw); };
  return (
    <form className="live-pw" onSubmit={submit} aria-label="Пароль трансляции">
      <Icon name="lock" size={22} />
      <h2>Трансляция защищена паролем</h2>
      <label>Пароль<input ref={ref} type="password" autoComplete="off" value={pw} onChange={(e) => setPw(e.target.value)} maxLength={256} aria-invalid={!!error} /></label>
      {error && <div className="alert error" role="alert">{error}</div>}
      <button className="btn primary" disabled={busy || !pw}>{busy ? "Проверка…" : "Смотреть"}</button>
    </form>
  );
}

/** Зритель представляется перед просмотром: имя обязательно, почта — по желанию (для протокола и материалов встречи). */
function ViewerForm({ token, title, onDone }: { token: string; title: string; onDone: (viewer: string) => void }) {
  const [c, setC] = useState<Contact>(loadContact);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const ok = nameOk(c) && emailOk(c);
  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!ok || busy) return;
    setBusy(true); setErr("");
    try { const r = await api.liveViewer(token, contactBody(c)); saveContact(c); onDone(r.viewer); }
    catch (x) { const a = x as ApiError; setErr(a.status === 429 ? "Слишком много попыток. Повторите позже." : a.message || "Не удалось продолжить."); }
    finally { setBusy(false); }
  };
  return (
    <form className="live-pw live-viewer" onSubmit={(e) => void submit(e)} aria-label="Представьтесь">
      <h2>{title}</h2>
      <p className="muted small">Представьтесь, чтобы смотреть эфир и задавать вопросы ведущему.</p>
      <GuestContactFields value={c} onChange={setC} onEnter={() => void submit()} />
      {err && <div className="alert error" role="alert">{err}</div>}
      <button className="btn primary" disabled={!ok || busy}>{busy ? "Подождите…" : "Смотреть"}</button>
    </form>
  );
}

const qStatus = (q: LiveQuestion) => q.status === "done" ? "Ведущий ответил" : q.status === "hidden" ? "Ведущий не будет отвечать в эфире" : q.seen ? "Получен ведущим" : "Отправлен";
const Q_MAX = 500;

/** Вопросы зрителя к идущему эфиру — боковая панель рядом с видео: свои вопросы и их состояние (отправлен → получен ведущим → ответил), поле внизу.
 *  Чужие вопросы зрителю не показываются. */
function ViewerQuestions({ token, viewer, pass, onViewerLost }: { token: string; viewer: string; pass: () => string; onViewerLost: () => void }) {
  const [text, setText] = useState("");
  const [items, setItems] = useState<LiveQuestion[]>([]);
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const prev = useRef<Map<string, string>>(new Map());
  const list = useRef<HTMLOListElement>(null);
  const load = useCallback(() => api.liveMyQuestions(token, viewer).then((r) => {
    // ведущий ответил на вопрос — заметное уведомление в панели (без прокрутки страницы)
    const answered = r.items.filter((q) => q.status === "done" && prev.current.has(q.id) && prev.current.get(q.id) !== "done");
    if (answered.length) setNote({ kind: "ok", text: answered.length === 1 ? "Ведущий ответил на ваш вопрос." : `Ведущий ответил на ваши вопросы: ${answered.length}.` });
    prev.current = new Map(r.items.map((q) => [q.id, q.status]));
    setItems(r.items); setOpen(r.open);
  }).catch((e) => { if ((e as ApiError).code === "viewer_required") onViewerLost(); }), [token, viewer, onViewerLost]);
  useEffect(() => {
    void load();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 10_000);
    return () => window.clearInterval(t);
  }, [load]);
  useEffect(() => { const el = list.current; if (el) el.scrollTop = el.scrollHeight; }, [items.length]);
  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true); setNote(null);
    try {
      const q = await api.liveAsk(token, { viewer, text: t, ...(pass() ? { pass: pass() } : {}) });
      prev.current.set(q.id, q.status);
      setItems((xs) => [...xs, q]); setText(""); setNote({ kind: "ok", text: "Вопрос отправлен ведущему." });
    } catch (x) {
      const a = x as ApiError;
      if (a.code === "viewer_required") onViewerLost();
      setNote({ kind: "error", text: a.code === "not_live" ? "Эфир сейчас не идёт: вопросы не принимаются." : a.message || "Не удалось отправить вопрос." });
    } finally { setBusy(false); }
  };
  return (
    <aside className="live-side card" aria-label="Вопросы ведущему">
      <header className="live-side-head"><b>Вопросы ведущему</b>{items.length > 0 && <span className="muted small">{items.length}</span>}</header>
      <ol className="live-q-mine" ref={list} aria-live="polite">
        {items.length === 0 && <li className="live-q-empty muted small">Задайте вопрос — ведущий увидит его во время эфира. Ваши вопросы и ответы на них появятся здесь.</li>}
        {items.map((q) => (
          <li key={q.id} className={q.status}><p>{q.text}</p><span className={`live-q-st ${q.status}${q.seen ? " seen" : ""}`}>{qStatus(q)}</span></li>
        ))}
      </ol>
      {note && <p className={`live-q-note small ${note.kind === "error" ? "field-error" : ""}`} role={note.kind === "error" ? "alert" : "status"}>{note.text}</p>}
      <form className="live-q-form" onSubmit={(e) => void send(e)}>
        <textarea id="live-q-text" rows={2} maxLength={Q_MAX} value={text} disabled={!open} aria-label="Ваш вопрос ведущему"
                  placeholder={open ? "Ваш вопрос ведущему" : "Вопросы принимаются, пока идёт эфир"}
                  onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
        <div className="row live-q-row">
          <span className="muted small" aria-live="off">{text.length}/{Q_MAX}</span>
          <span className="spacer" />
          <button className="btn primary" disabled={!open || busy || !text.trim()}>{busy ? "Отправка…" : "Отправить"}</button>
        </div>
      </form>
    </aside>
  );
}

/** Плеер: нативный HLS (Safari, iPhone, iPad) или hls.js (остальные). Свои кнопки: пауза, звук, возврат к эфиру, полный экран. */
/** Просмотр по WebRTC: та же сцена, что идёт в эфир, из комнаты напрямую (задержка меньше секунды). Нет дорожки за 8 с или соединение оборвалось — зритель уходит на HLS. */
function RtcView({ token, rtc, vid, onFail }: { token: string; rtc: NonNullable<LiveAccess["rtc"]>; vid: string; onFail: () => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [muted, setMuted] = useState(false);
  const fail = useRef(onFail);
  fail.current = onFail;
  useEffect(() => {
    const timer = window.setTimeout(() => fail.current(), 8000);
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== window.location.origin || e.source !== frame.current?.contentWindow) return;
      if (e.data?.type === "rtc-ok") window.clearTimeout(timer);
      if (e.data?.type === "rtc-fail") fail.current();
      if (e.data?.type === "rtc-muted") setMuted(true);
      if (e.data?.type === "rtc-sound-on") setMuted(false);
    };
    window.addEventListener("message", onMsg);
    const ping = () => { if (document.visibilityState === "visible") void api.livePing(token, vid).catch(() => undefined); };
    ping();
    const t = window.setInterval(ping, 30_000);
    return () => { window.clearTimeout(timer); window.clearInterval(t); window.removeEventListener("message", onMsg); };
  }, [token, vid]);
  const src = `/rec/template?view=1&url=${encodeURIComponent(rtc.url)}&token=${encodeURIComponent(rtc.token)}&rt=${encodeURIComponent(rtc.rt)}`;
  return (
    <div className="live-stage ui">
      <iframe ref={frame} src={src} title="Прямой эфир" allow="autoplay; fullscreen" className="live-rtc" />
      {muted && <button className="btn primary live-sound" onClick={() => frame.current?.contentWindow?.postMessage({ type: "rtc-sound" }, window.location.origin)}>Включить звук</button>}
    </div>
  );
}

function Player({ token, access, renew, onGone, skew }: { token: string; access: LiveAccess; renew: (avoid?: string, wantLow?: boolean) => Promise<LiveAccess | null>; onGone: () => void; skew: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const prefix = useRef<string>(access.path.replace(/[^/]+$/, ""));
  const baseRef = useRef<string>(access.base ?? "");                 // CDN; при его недоступности плеер сам переходит на сервер-источник (пусто)
  const nodeRef = useRef<string>(access.node ?? "");                 // свой узел CDN, с которого идёт поток
  const llOff = useRef(false);                                       // низкая задержка не работает у этого зрителя: остаёмся на обычном HLS до конца просмотра
  const originRef = useRef<string>(access.origin_path ?? "");        // запасной путь к серверу-источнику (узлы CDN)
  const badNodes = useRef(new Map<string, number>());                // узел → когда не отдал поток: 90 с к нему не возвращаемся (сервер исключает узел через 30 с)
  const [paused, setPaused] = useState(true);
  const [muted, setMuted] = useState(false);
  const [vol, setVol] = useState(1);
  const [behind, setBehind] = useState(0);
  const [needSound, setNeedSound] = useState(false);
  const [buffering, setBuffering] = useState(true);
  const [fatal, setFatal] = useState("");
  const [ui, setUi] = useState(true);
  const hideT = useRef<number>(0);
  const fails = useRef(0);                       // подряд идущие сбои сети плеера; сбрасывается, когда воспроизведение пошло
  const hlsRef = useRef<{ destroy: () => void; recoverMediaError: () => void; startLoad: () => void; loadSource?: (u: string) => void; liveSyncPosition?: number | null } | null>(null);
  // автоматическое качество: видео ↔ «только звук» (правила и пороги — live/adaptive.ts)
  const saved = useRef<Choice>(loadChoice());                        // выбор прошлого просмотра: «только звук» применяется сразу, «низкое» — когда сервер его подготовит
  const startAudio = saved.current === "audio" && !!access.audio_path;
  const [mode, setMode] = useState<Mode>(startAudio ? "audio" : "video");
  const [modeWhy, setModeWhy] = useState(startAudio ? "Только звук: вы выбрали этот режим." : "");
  const modeRef = useRef<Mode>(startAudio ? "audio" : "video");
  const [choice, setChoice] = useState<Choice>(access.audio_path ? saved.current : "auto");
  const choiceRef = useRef<Choice>(access.audio_path ? saved.current : "auto");
  const lowOk = useRef(!!access.low_path);                           // сервер готовит низкое качество (360p): оно запускается по запросу зрителя
  const lowMiss = useRef(access.low_path ? 0 : 2);
  const lowSeen = useRef(access.low_path ? Date.now() : 0);          // когда сервер последний раз подтвердил низкое качество: процесс живёт 90 с после последнего запроса, старым данным верить нельзя
  const lowFresh = () => lowOk.current && Date.now() - lowSeen.current < 45_000;
  const lowNeedRef = useRef(access.low_need_kbps ?? 720);
  const lastWant = useRef(0);
  const [sw, setSw] = useState<Switch | null>(null);                 // идёт переключение: баннер «подождите» и закрытые кнопки качества
  const swRef = useRef<Switch | null>(null);
  const [slow, setSlow] = useState(false);
  const [menu, setMenu] = useState(false);
  const [pill, setPill] = useState("");                              // короткое спокойное сообщение поверх картинки
  const pillT = useRef(0);
  const lastAdvice = useRef(0);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; window.clearTimeout(pillT.current); }, []);
  const audioOk = useRef(!!access.audio_path);                       // сервер предлагает дорожку со звуком (низкая задержка и служба ретрансляции работают)
  const [hasAudio, setHasAudio] = useState(!!access.audio_path);
  const audioMiss = useRef(access.audio_path ? 0 : 2);
  const needRef = useRef(access.need_kbps ?? 3500);                 // сколько нужно видео (из настроек качества эфира)
  const ad = useRef<AdaptiveState>(adaptiveInitial(performance.now(), startAudio ? { mode: "audio", manual: true } : { manual: access.audio_path ? saved.current === "video" || saved.current === "low" : false }));
  const probe = useRef<{ at: number; kbps: number | null; busy: boolean }>({ at: 0, kbps: null, busy: false });
  const [attempt, setAttempt] = useState(0);     // пересоздание плеера: страж зависания, «Повторить», возврат сети
  const [offline, setOffline] = useState(typeof navigator !== "undefined" && navigator.onLine === false);
  const userPaused = useRef(false);
  const cb = useRef({ renew, onGone });
  cb.current = { renew, onGone };
  const stats = useRef(new ViewerStats());       // показатели этого зрителя: уходят с пульсом, на экране — по кнопке «Статистика»
  const [diag, setDiag] = useState(false);
  const [diagMeeting, setDiagMeeting] = useState<string | null>(null);
  stats.current.skew = skew;
  stats.current.node = nodeRef.current;

  // новая ссылка: узел CDN (или источник) и префикс подписи; адреса сегментов меняются без перезапуска плеера
  const isBad = (node: string) => Date.now() - (badNodes.current.get(node) ?? 0) < 90_000;
  function adopt(a: LiveAccess) {
    if (a.node && a.origin_path && isBad(a.node)) {          // продление раньше, чем сервер исключил узел: остаёмся на источнике
      baseRef.current = ""; nodeRef.current = ""; originRef.current = a.origin_path; prefix.current = a.origin_path.replace(/[^/]+$/, "");
      return;
    }
    // свои узлы и источник: решает сервер (узел восстановился — зритель возвращается на него). Внешний CDN (base без node): если плеер
    // уже ушёл на источник, он там и остаётся — как раньше.
    if (a.node || !a.base) { baseRef.current = a.base ?? ""; nodeRef.current = a.node ?? ""; }
    originRef.current = a.origin_path ?? "";
    // дорожки может не оказаться в одном ответе (проверка готовности не успела): отказываемся от неё только после двух подряд
    audioMiss.current = a.audio_path ? 0 : audioMiss.current + 1;
    audioOk.current = audioMiss.current < 2; setHasAudio(audioOk.current);
    if (a.need_kbps) needRef.current = a.need_kbps;
    if (a.low_path) lowSeen.current = Date.now();
    lowMiss.current = a.low_path ? 0 : lowMiss.current + 1;
    lowOk.current = lowMiss.current < 2;
    if (a.low_need_kbps) lowNeedRef.current = a.low_need_kbps;
    prefix.current = (llOff.current && a.origin_path ? a.origin_path : a.path).replace(/[^/]+$/, "");
  }
  // свой узел CDN не отвечает: сервер выдаёт другой исправный узел; если его нет — запасной путь к источнику. Возвращает адрес плейлиста или "".
  async function failover(): Promise<string> {
    const failed = nodeRef.current;
    badNodes.current.set(failed, Date.now());
    const a = await cb.current.renew(failed || undefined).catch(() => null);
    if (a && a.node && !isBad(a.node)) { adopt(a); return baseRef.current + prefix.current + "index.m3u8"; }
    const o = a?.origin_path || originRef.current;
    if (!o) return "";
    baseRef.current = ""; nodeRef.current = ""; prefix.current = o.replace(/[^/]+$/, "");
    return o;
  }

  // Переключение дорожки: тот же плеер, другой плейлист той же подписи (/audio/ — только звук, /low/ — низкое качество 360p). Пока новый поток не пошёл, зритель видит баннер «подождите».
  const urlFor = (m: Mode) => baseRef.current + prefix.current + (m === "audio" ? "audio/" : m === "low" ? "low/" : "") + "index.m3u8";
  function flash(text: string, ms = 10_000) {
    window.clearTimeout(pillT.current); setPill(text);
    if (text) pillT.current = window.setTimeout(() => setPill(""), ms);
  }
  function applyMode(m: Mode, why: string) {
    const from = modeRef.current;
    modeRef.current = m; setMode(m); setModeWhy(why); flash(m === "audio" ? "" : why);
    stats.current.s.aud = m === "audio" ? 1 : 0; stats.current.s.low = m === "low" ? 1 : 0;
    const el = video.current;
    if (el) el.dataset.mode = m;
    if (from !== m) { const x: Switch = { to: m, from, at: Date.now(), t0: el?.currentTime ?? 0 }; swRef.current = x; setSw(x); setSlow(false); }
    hlsRef.current?.loadSource?.(urlFor(m));
  }
  // Низкое качество готовится по запросу: просим сервер и ждём ссылку до минуты. Звук всё это время идёт без остановок.
  async function ensureLow(): Promise<boolean> {
    for (let i = 0; i < 24 && alive.current; i++) {
      const a = await cb.current.renew(undefined, true).catch(() => null);
      if (a) { adopt(a); if (a.low_path) return true; }
      await new Promise((r) => window.setTimeout(r, 2500));
    }
    return false;
  }
  async function goTo(m: Mode, why: string): Promise<boolean> {
    if (m === "low") {
      // процесс низкого качества живёт, только пока он нужен: перед переключением всегда спрашиваем сервер заново (запрос же и запускает его)
      const a0 = await cb.current.renew(undefined, true).catch(() => null);
      if (a0) adopt(a0);
      if (!alive.current) return false;
    }
    if (m === "low" && !(lowOk.current && lowMiss.current === 0)) {
      const x: Switch = { to: m, from: modeRef.current, at: Date.now(), t0: video.current?.currentTime ?? 0, wait: true };
      swRef.current = x; setSw(x); setSlow(false);
      const ok = await ensureLow();
      if (!alive.current) return false;
      if (!ok) { swRef.current = null; setSw(null); flash("Низкое качество пока не готово. Эфир продолжается: попробуйте ещё раз чуть позже или выберите «Только звук».", 14_000); return false; }
    }
    applyMode(m, why);
    return true;
  }
  // Скорость канала для решения «вернуться к видео»: пробная загрузка последнего завершённого видеосегмента (на дорожке со звуком оценки hls.js нет)
  async function probeSpeed() {
    const p = probe.current;
    if (p.busy) return;
    p.busy = true;
    try {
      const base = baseRef.current + prefix.current;
      const list = async (name: string) => (await (await fetch(`${base}${name}`, { cache: "no-store" })).text()).split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
      let uris = await list("index.m3u8");
      if (uris[0]?.endsWith(".m3u8")) uris = await list(uris[0]);          // главный плейлист ведёт к плейлисту видео: сегменты — в нём
      const uri = uris[uris.length - 2] ?? uris[uris.length - 1];
      if (!uri || uri.endsWith(".m3u8")) { p.kbps = null; return; }
      const t0 = performance.now();
      const buf = await (await fetch(`${base}${uri}`, { cache: "no-store" })).arrayBuffer();
      const ms = Math.max(20, performance.now() - t0);                    // на быстром канале замер короче 20 мс неточен: берём нижнюю оценку скорости
      p.kbps = buf.byteLength > 10_000 ? (buf.byteLength * 8) / ms : null;
    } catch { p.kbps = null; } finally { p.busy = false; p.at = Date.now(); }
  }
  // подпись продлевается заранее; адреса сегментов получают свежий префикс без перезапуска плеера
  useEffect(() => {
    let alive = true;
    let timer = window.setTimeout(function tick() {
      void renew(undefined, modeRef.current === "low").then((a) => {
        if (!alive || !a) return;
        adopt(a);
        const el = video.current;
        if (el && native()) { const was = !el.paused; el.src = baseRef.current + prefix.current + "index.m3u8"; if (was) void el.play().catch(() => undefined); }        // нативный HLS подписи не подменяет: источник меняется (редко: подпись для Safari живёт часы)
      }).finally(() => { if (alive) timer = window.setTimeout(tick, Math.max(60, access.refresh_in) * 1000); });
    }, Math.max(60, access.refresh_in) * 1000);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [access.refresh_in, renew]);

  // Автоматическое качество: раз в секунду смотрим на приём и решаем, какая ступень (видео, низкое качество, только звук) подходит (все признаки — в live/adaptive.ts).
  // Если зритель выбрал качество сам, режим не меняется: автоматика только подсказывает.
  useEffect(() => {
    if (native()) return undefined;
    let first = true;
    let drop = { t: 0, d: 0, f: 0 };
    let dropRatio: number | null = null;
    const timer = window.setInterval(() => {
      const el = video.current;
      if (!el) return;
      el.dataset.ll = llOff.current ? "off" : "on"; el.dataset.ad = `${ad.current.mode}${ad.current.manual ? "m" : ""}`; el.dataset.fails = String(fails.current);      // для проверок и отладки
      if (!audioOk.current || llOff.current || baseRef.current) {         // дорожек больше нет (служба остановилась, переход на обычный HLS или CDN): только видео
        if (modeRef.current !== "video") { applyMode("video", ""); ad.current = adaptiveInitial(performance.now(), { stalls: stats.current.s.stalls, stallMs: stats.current.s.stall_ms }); }
        return;
      }
      if (modeRef.current === "low" && !lowOk.current) {                  // низкое качество пропало (процесс остановился): спокойно уходим на звук
        applyMode("audio", "Низкое качество стало недоступно, поэтому включён режим «Только звук». Видео вернётся само, как только связь наладится.");
        ad.current = adaptiveInitial(performance.now(), { stalls: stats.current.s.stalls, stallMs: stats.current.s.stall_ms, mode: "audio", manual: choiceRef.current !== "auto" });
        return;
      }
      // не меряем, пока зритель на паузе или воспроизведение ещё не начиналось; остановки посреди эфира (readyState 1) как раз и надо замечать
      if (userPaused.current || el.paused || (el.readyState < 2 && el.currentTime === 0)) { first = true; return; }
      if (swRef.current?.wait) return;                                    // ждём низкое качество от сервера: решения не принимаем
      const s = stats.current.tick();
      const now = performance.now();
      if (first) { ad.current.prevStalls = s.stalls; ad.current.prevStallMs = s.stall_ms; first = false; drop = { t: now, d: s.drop ?? 0, f: s.frames ?? 0 }; ad.current.graceUntil = Math.max(ad.current.graceUntil, now + 8000); }      // первые секунды набирается буфер: это не плохая связь
      if (now - drop.t >= 10_000) { const df = (s.frames ?? 0) - drop.f; dropRatio = df > 30 ? ((s.drop ?? 0) - drop.d) / df : null; drop = { t: now, d: s.drop ?? 0, f: s.frames ?? 0 }; }
      const conn = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
      const hidden = document.visibilityState !== "visible";
      const m = modeRef.current;
      const sample: Sample = {
        buffer: s.buf ?? 3, stalls: s.stalls, stallMs: s.stall_ms, bwKbps: m !== "audio" && s.est ? s.est : null, probeKbps: probe.current.kbps,
        fragRatio: null, dropRatio: m !== "audio" ? dropRatio : null, hidden, saveData: !!conn?.saveData, slowNet: /(^|-)2g$/.test(conn?.effectiveType ?? ""), needKbps: needRef.current,
        lowNeedKbps: lowFresh() ? lowNeedRef.current : null,       // автоматика не ждёт сервер: нет свежего подтверждения низкого качества — при ухудшении сразу звук, а низкое качество греется в фоне
      };
      if (m !== "video" && !hidden && now >= ad.current.holdUntil - 5000 && Date.now() - probe.current.at > 10_000) void probeSpeed();
      // низкое качество готовится заранее, пока видео ещё идёт, но уже есть тревожные признаки: к моменту ухудшения оно будет готово
      if (choiceRef.current === "auto" && m === "video" && !lowFresh() && !swRef.current && Date.now() - lastWant.current > 30_000
        && (ad.current.stallLog.length >= 1 || sample.buffer < 1.5 || (sample.bwKbps !== null && sample.bwKbps < needRef.current * 1.6))) {
        lastWant.current = Date.now();
        void cb.current.renew(undefined, true).then((a) => { if (a) adopt(a); });
      }
      el.dataset.probe = String(Math.round(probe.current.kbps ?? -1)); el.dataset.hold = String(Math.round((ad.current.holdUntil - now) / 1000));      // для проверок и отладки
      const d = adaptiveStep(ad.current, sample, now);
      if (d.advice && now > lastAdvice.current + 90_000) { lastAdvice.current = now; flash("Картинка подвисает. Выберите качество ниже в меню ⚙ или режим «Авто»: звук при этом не пропадёт.", 14_000); }
      if (d.changed) {
        const to = d.mode;
        void goTo(to, d.up ? upNote(to) : to === "audio" ? audioNote(d.reason) : lowNote(d.reason)).then((ok) => { if (!ok) ad.current.mode = modeRef.current; });
      }
    }, 1000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  // Переключение завершено, когда новый поток действительно пошёл: время идёт вперёд и размер картинки соответствует ступени. Долго нет — спокойное пояснение, совсем долго — возврат.
  useEffect(() => {
    if (!sw || sw.wait) return undefined;
    const t = window.setInterval(() => {
      const x = swRef.current, el = video.current;
      if (!x || !el) return;
      const waited = Date.now() - x.at, h = el.videoHeight;
      const shape = x.to === "audio" ? true : x.to === "low" ? h > 0 && h <= 420 : h > 420;
      // у каждой ступени своя шкала времени: после смены потока currentTime может скакнуть назад, поэтому ход времени считаем от момента, когда пришла картинка нужного размера
      if (waited > 1000 && shape && x.base === undefined) x.base = el.currentTime;
      if (x.base !== undefined && !el.paused && el.readyState >= 3 && Math.abs(el.currentTime - x.base) >= 0.6) { swRef.current = null; setSw(null); setSlow(false); return; }
      if (waited > 15_000) setSlow(true);
      if (waited > 50_000) {
        swRef.current = null; setSw(null); setSlow(false);
        const back: Mode = x.from;
        flash(`Не удалось включить ${MODE_NAME[x.to]}: оставляем ${MODE_NAME[back]}. Эфир продолжается, попробуйте позже.`, 14_000);
        modeRef.current = back; setMode(back); setModeWhy(back === "audio" ? "Только звук." : "");
        stats.current.s.aud = back === "audio" ? 1 : 0; stats.current.s.low = back === "low" ? 1 : 0;
        ad.current.mode = back; ad.current.graceUntil = performance.now() + 10_000;
        hlsRef.current?.loadSource?.(urlFor(back));
      }
    }, 500);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sw]);

  // Ручной выбор качества. «Авто» начинает с ступени, которую позволяет канал (пробная загрузка), остальное включается как выбрано и не меняется само.
  const choose = async (c: Choice) => {
    if (swRef.current) return;                                // идёт переключение: ждём, чтобы кнопки не конфликтовали
    setMenu(false); setChoice(c); choiceRef.current = c; saveChoice(c);
    const now = performance.now(), st = stats.current.s, seed = { stalls: st.stalls, stallMs: st.stall_ms };
    if (c === "auto") {
      let to = modeRef.current;
      if (to !== "video") {
        await probeSpeed();
        const sp = probe.current.kbps;
        to = sp === null || sp >= needRef.current * 1.2 ? "video" : lowOk.current ? "low" : "audio";
      }
      ad.current = adaptiveInitial(performance.now(), { ...seed, mode: to, grace: true });
      if (to !== modeRef.current) { const ok = await goTo(to, to === "video" ? "" : lowNote("связь пока слабая")); if (!ok) ad.current.mode = modeRef.current; }
      else flash("Режим «Авто»: качество подбирается по вашей связи.");
      return;
    }
    ad.current = adaptiveInitial(now, { ...seed, mode: c, manual: true, grace: true });
    if (c !== modeRef.current) { const ok = await goTo(c, c === "audio" ? "Только звук: вы выбрали этот режим. Вернуть видео можно в меню качества." : ""); if (!ok) ad.current.mode = modeRef.current; }
  };
  // «Включить видео» на экране звука: пробуем видео в режиме «Авто» (если связь всё ещё слабая, плеер сам вернёт ступень ниже, но не раньше чем через 10 секунд после загрузки)
  const tryVideo = () => {
    if (swRef.current) return;
    setChoice("auto"); choiceRef.current = "auto"; saveChoice("auto");
    const st = stats.current.s;
    ad.current = adaptiveInitial(performance.now(), { stalls: st.stalls, stallMs: st.stall_ms, mode: "video", grace: true });
    probe.current.kbps = null;
    void goTo("video", "");
  };
  useEffect(() => {
    if (saved.current !== "low" || !access.audio_path) return undefined;
    const t = window.setTimeout(() => { if (choiceRef.current === "low" && modeRef.current === "video") { void choose("low"); } }, 2500);     // сохранённое «низкое»: ждём, пока плеер запустится
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!menu) return undefined;
    const close = (e: Event) => { if (!(e.target as HTMLElement).closest?.(".live-q")) setMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("pointerdown", close); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", esc); };
  }, [menu]);

  // счётчик зрителей: «пульс» раз в полминуты, пока вкладка видима
  useEffect(() => {
    const ping = () => { if (document.visibilityState === "visible") void api.livePing(token, access.vid, stats.current.tick()).catch(() => undefined); };
    ping();
    const t = window.setInterval(ping, 30_000);
    // проблема у зрителя видна организатору сразу, а не через полминуты; не чаще раза в 10 с
    let last = 0;
    stats.current.onProblem = () => { const now = Date.now(); if (now - last > 10_000) { last = now; window.setTimeout(ping, 1500); } };
    return () => { window.clearInterval(t); stats.current.onProblem = null; };
  }, [token, access.vid]);

  useEffect(() => {
    const el = video.current;
    if (!el) return;
    let cancelled = false;
    const url = modeRef.current === "audio" && audioOk.current && !baseRef.current ? prefix.current + "audio/index.m3u8" : baseRef.current + access.path;
    el.muted = false;
    el.dataset.engine = native() ? "native" : "hlsjs";           // для проверок: какой механизм воспроизведения выбран
    const start = () => el.play().catch(() => { el.muted = true; setMuted(true); setNeedSound(true); return el.play().catch(() => undefined); });
    if (native()) {
      el.src = url;
      el.addEventListener("loadedmetadata", () => void start(), { once: true });
      el.addEventListener("error", () => {
        fails.current += 1;
        if (baseRef.current && nodeRef.current && fails.current >= 2) { fails.current = 0; void failover().then((u) => { if (u) { el.src = u; void start(); } }); return; }   // узел CDN не отвечает
        if (baseRef.current && fails.current >= 2) { baseRef.current = ""; fails.current = 0; el.src = access.path; void start(); return; }         // CDN недоступна: источник
        if (fails.current > 5) { setFatal("Не удаётся получить поток эфира. Нажмите «Повторить» или попробуйте позже."); return; }
        window.setTimeout(() => void renew().then((a) => { if (!a) return onGone(); adopt(a); el.src = baseRef.current + prefix.current + "index.m3u8"; void start(); }), Math.min(15_000, 1000 * 2 ** (fails.current - 1)));
      });
    } else {
      void import("hls.js").then(({ default: Hls }) => {
        if (cancelled) return;
        if (!Hls.isSupported()) { setFatal(inDesktopClient() ? "Не удалось запустить просмотр эфира. Перезапустите приложение; если не помогло, откройте эфир по ссылке в другом браузере и сообщите администратору." : "Этот браузер не умеет воспроизводить прямой эфир. Попробуйте другой браузер."); return; }
        const hls = new Hls({
          // сегменты ≈1,5 с (без частей LL-HLS — Egress их не выпускает): держимся в 2,5 с от края эфира — в секундах, а не в сегментах, потому что
          // TARGETDURATION плейлиста 2 (см. nginx.conf); отставание догоняем ускорением до 1,1×, а не прыжком; дальше 7 с — прыжок к краю.
          // Было 3 сегмента по 2 с — зритель отставал на 11 с.
          // LL-HLS (путь /llhls/): части сегмента по 0,2 с, держимся в секунде от края. Замер — 2,9 с от выступающего до зрителя.
          enableWorker: false, lowLatencyMode: isLl(url), liveSyncDuration: isLl(url) ? 1 : 2.5, liveMaxLatencyDuration: isLl(url) ? 4 : 7, maxLiveSyncPlaybackRate: isLl(url) ? 1.2 : 1.1, maxBufferLength: 10, backBufferLength: 10,
          manifestLoadPolicy: { default: { maxTimeToFirstByteMs: 10000, maxLoadTimeMs: 20000, timeoutRetry: { maxNumRetry: 4, retryDelayMs: 1000, maxRetryDelayMs: 4000 }, errorRetry: { maxNumRetry: 6, retryDelayMs: 1000, maxRetryDelayMs: 4000 } } },
          xhrSetup: (xhr: XMLHttpRequest, u: string) => { xhr.open("GET", u.replace(HLS_PREFIX, baseRef.current + prefix.current), true); },
        });
        hlsRef.current = hls as never;
        stats.current.attachHls(hls as never, Hls.Events);
        hls.loadSource(url);
        hls.attachMedia(el);
        hls.on(Hls.Events.MANIFEST_PARSED, () => void start());
        // низкая задержка готовится первые секунды эфира: как только сервер её предложит, плеер переходит на неё сам
        let tries = access.ll_soon ? 12 : 0;
        const upgrade = () => {
          if (cancelled || tries-- <= 0) return;
          void cb.current.renew().then((a) => {
            if (cancelled) return;
            if (!a || !isLl(a.path)) { window.setTimeout(upgrade, 3000); return; }
            adopt(a);
            hls.config.lowLatencyMode = true; hls.config.liveSyncDuration = 1; hls.config.liveMaxLatencyDuration = 4; hls.config.maxLiveSyncPlaybackRate = 1.2;
            hls.loadSource(a.path);
          });
        };
        window.setTimeout(upgrade, 3000);
        hls.on(Hls.Events.ERROR, (_e, d) => {
          if (!d.fatal) return;
          const code = (d as { response?: { code?: number } }).response?.code;
          if (d.type === Hls.ErrorTypes.NETWORK_ERROR) {
            if (isLl(prefix.current) && originRef.current && fails.current >= 1) {
              // служба низкой задержки недоступна: тот же эфир обычным HLS, зритель потеряет пару секунд, но не поток
              llOff.current = true;
              prefix.current = originRef.current.replace(/[^/]+$/, ""); fails.current = 0;
              hls.config.lowLatencyMode = false; hls.config.liveSyncDuration = 2.5; hls.config.liveMaxLatencyDuration = 7;
              hls.loadSource(originRef.current);
              return;
            }
            // повторы с нарастающей паузой и пределом: сломанный адрес не должен превращаться в шквал запросов к приложению
            if (baseRef.current && nodeRef.current && (!code || code >= 500 || code === 429) && fails.current >= 1) {
              // свой узел CDN не отвечает: другой узел или сервер-источник; зритель увидит короткую паузу
              fails.current = 0;
              void failover().then((u) => { if (u) hls.loadSource(u); else window.setTimeout(() => hls.startLoad(), 3000); });
              return;
            }
            if (baseRef.current && (!code || code >= 500) && fails.current >= 1) {
              // CDN недоступна (нет ответа или 5xx): те же подписанные адреса берём прямо с сервера-источника, зритель этого почти не заметит
              baseRef.current = "";
              fails.current = 0;
              hls.loadSource(access.path);
              return;
            }
            fails.current += 1;
            if (fails.current > 6) { setFatal("Не удаётся получить поток эфира. Нажмите «Повторить» или попробуйте позже."); return; }
            const wait = Math.min(15_000, 1000 * 2 ** (fails.current - 1));
            // плейлист ещё не загружался (404, пока Egress его не записал; обрыв на первом запросе) — startLoad() его уже не запросит: источник загружается заново
            const resume = () => (hls.levels?.length ? hls.startLoad() : hls.loadSource(baseRef.current + prefix.current + "index.m3u8"));
            if (code === 404 || code === 403 || code === 410) { window.setTimeout(() => void renew().then((a) => { if (!a) onGone(); else { adopt(a); resume(); } }), wait); }
            else window.setTimeout(resume, wait);
          } else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
          else setFatal("Не удалось воспроизвести эфир.");
        });
      }).catch(() => setFatal("Не удалось загрузить плеер."));
    }
    const offStats = stats.current.attachVideo(el, native());
    return () => { cancelled = true; offStats(); hlsRef.current?.destroy(); hlsRef.current = null; el.removeAttribute("src"); el.load(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access.path.split("/")[2], attempt]);          // источник пересоздаётся при смене идентификатора доступа (не при продлении подписи) и при переподключении

  // страж: эфир идёт, зритель не ставил паузу, а картинка не двигается 20 с без всякой ошибки (поток «завис») — плеер переподключается сам;
  // после 5 неудачных переподключений подряд — сообщение с кнопкой «Повторить». Пока сети нет, не переподключаемся: ждём её возврата.
  useEffect(() => {
    let last = -1, since = Date.now(), tries = 0;
    const t = window.setInterval(() => {
      const el = video.current;
      if (!el || userPaused.current || document.visibilityState !== "visible" || navigator.onLine === false) { since = Date.now(); return; }
      if (el.currentTime !== last) { last = el.currentTime; since = Date.now(); if (!el.paused) tries = 0; return; }
      if (Date.now() - since < 20_000) return;
      since = Date.now();
      tries += 1;
      if (tries > 5) { setFatal("Эфир не воспроизводится: поток не поступает. Проверьте подключение к интернету и нажмите «Повторить»."); return; }
      stats.current.reconnect();
      void cb.current.renew().then((a) => { if (!a) return cb.current.onGone(); adopt(a); fails.current = 0; setAttempt((n) => n + 1); });
    }, 5000);
    const off = () => setOffline(true);
    const on = () => { setOffline(false); setFatal(""); fails.current = 0; stats.current.reconnect(); setAttempt((n) => n + 1); };
    window.addEventListener("offline", off); window.addEventListener("online", on);
    return () => { window.clearInterval(t); window.removeEventListener("offline", off); window.removeEventListener("online", on); };
  }, []);

  // отставание от прямого эфира и состояние кнопок
  useEffect(() => {
    const el = video.current;
    if (!el) return;
    const upd = () => {
      setPaused(el.paused); setMuted(el.muted || el.volume === 0); setVol(el.volume);
      const end = el.seekable.length ? el.seekable.end(el.seekable.length - 1) : 0;
      setBehind(end ? Math.max(0, end - el.currentTime) : 0);
    };
    const on = ["play", "pause", "volumechange", "timeupdate", "progress"] as const;
    on.forEach((e) => el.addEventListener(e, upd));
    const wait = () => setBuffering(true), ready = () => { setBuffering(false); fails.current = 0; };
    el.addEventListener("waiting", wait); el.addEventListener("playing", ready); el.addEventListener("canplay", ready);
    return () => { on.forEach((e) => el.removeEventListener(e, upd)); el.removeEventListener("waiting", wait); el.removeEventListener("playing", ready); el.removeEventListener("canplay", ready); };
  }, []);

  const poke = () => { setUi(true); window.clearTimeout(hideT.current); hideT.current = window.setTimeout(() => setUi(false), 3500); };
  const toggle = () => { const el = video.current; if (!el) return; if (el.paused) { userPaused.current = false; void el.play(); } else { userPaused.current = true; el.pause(); } poke(); };
  const goLive = () => { const el = video.current; if (!el) return; const end = el.seekable.length ? el.seekable.end(el.seekable.length - 1) : 0; if (end) el.currentTime = end - 1; void el.play(); poke(); };
  const unmute = () => { const el = video.current; if (!el) return; el.muted = false; el.volume = el.volume || 1; setNeedSound(false); void el.play(); };
  const fullscreen = () => {
    const w = wrap.current as (HTMLDivElement & { webkitRequestFullscreen?: () => void }) | null, el = video.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
    if (document.fullscreenElement) { void document.exitFullscreen(); return; }
    if (w?.requestFullscreen) void w.requestFullscreen().catch(() => undefined);
    else if (el?.webkitEnterFullscreen) el.webkitEnterFullscreen();            // iPhone: полноэкранный режим только у самого видео
    else if (w?.webkitRequestFullscreen) w.webkitRequestFullscreen();
  };

  if (fatal) return <div className="live-stage live-msg"><Icon name="info" size={26} /><p>{fatal}</p>
    <button className="btn primary" onClick={() => { setFatal(""); fails.current = 0; void renew().then((a) => { if (!a) return onGone(); adopt(a); setAttempt((n) => n + 1); }); }}>Повторить</button></div>;
  const overlay = mode === "audio" || sw?.from === "audio";            // весь экран закрыт сообщением: звук или переключение со звука на картинку
  return (
    <div className={`live-stage ${ui || paused ? "ui" : ""} ${diag ? "diag" : ""}`} ref={wrap} onMouseMove={poke} onTouchStart={poke}>
      <video ref={video} playsInline autoPlay aria-label="Прямой эфир" onClick={toggle} />
      {overlay && (sw
        ? <div className="live-audio" role="status"><div className="live-spin small" aria-hidden /><p><b>{sw.wait ? "Готовим низкое качество" : SWITCH_TEXT[sw.to]}…</b></p>
            <p className="live-sub">{sw.wait ? "На сервере это занимает до 30 секунд. Звук идёт без остановок, ничего нажимать не нужно." : slow ? "Загрузка идёт дольше обычного. Эфир не прерывается: просто подождите ещё немного." : "Это займёт несколько секунд, картинка появится сама. Ничего нажимать не нужно."}</p></div>
        : <div className="live-audio" role="status"><Icon name="volume" size={26} /><p>{modeWhy || "Только звук"}</p>
            <button className="btn primary" onClick={tryVideo}>Включить видео</button>
            <p className="live-sub">Если связь слабая, картинка может подвисать: тогда выберите «Низкое качество» в меню <Icon name="gear" size={14} /></p></div>)}
      {!overlay && (sw || pill || mode === "low") && <p className="live-pill" role="status"><span>{sw ? `${sw.wait ? "Готовим низкое качество" : SWITCH_TEXT[sw.to]}…${slow ? " Это дольше обычного, эфир не прерывается." : " Подождите несколько секунд."}` : pill || "Низкое качество: вернётся само, когда связь наладится"}</span></p>}
      {buffering && !sw && <div className="live-spin" role="status" aria-label="Загрузка" />}
      {offline && <p className="live-net" role="status">Нет подключения к сети. Эфир продолжится автоматически, когда связь восстановится.</p>}
      {needSound && <button className="live-sound btn primary" onClick={unmute}><Icon name="volumeOff" size={18} /> Включить звук</button>}
      <div className="live-bar" role="group" aria-label="Управление просмотром">
        <button className="iconbtn" onClick={toggle} aria-label={paused ? "Смотреть" : "Пауза"}><Icon name={paused ? "play" : "pause"} size={22} /></button>
        <button className="iconbtn" onClick={() => { const el = video.current; if (el) { el.muted = !el.muted; if (!el.muted && el.volume === 0) el.volume = 1; } }} aria-label={muted ? "Включить звук" : "Выключить звук"}><Icon name={muted ? "volumeOff" : "volume"} size={22} /></button>
        <input className="live-vol" type="range" min={0} max={1} step={0.05} value={muted ? 0 : vol} aria-label="Громкость" onChange={(e) => { const el = video.current; if (el) { el.volume = Number(e.target.value); el.muted = el.volume === 0; } }} />
        <span className={`live-tag ${behind > 8 ? "late" : "on"}`} title={behind > 8 ? `Вы отстаёте от эфира на ${Math.round(behind)} с` : "Вы смотрите прямой эфир"}><i aria-hidden />{behind > 8 ? `−${Math.round(behind)} с` : "ЭФИР"}</span>
        {behind > 8 && <button className="btn mini" onClick={goLive}>К прямому эфиру</button>}
        <span className="spacer" />
        {hasAudio && <div className="live-q">
          <button className="iconbtn" onClick={() => setMenu(!menu)} aria-haspopup="menu" aria-expanded={menu} aria-label="Качество просмотра" title="Качество просмотра"><Icon name="gear" size={22} /></button>
          {menu && <div className="live-menu" role="menu" aria-label="Качество просмотра">
            {QUALITY.map((q) => <button key={q.id} type="button" role="menuitemradio" aria-checked={choice === q.id} disabled={!!sw} onClick={() => void choose(q.id)}>
              <span className="qn">{q.name}{q.id === "auto" ? ` · сейчас: ${MODE_NAME[mode]}` : ""}</span><span className="qs">{q.sub}</span>{choice === q.id && <Icon name="check" size={16} />}
            </button>)}
            <p className="qh">{sw ? "Идёт переключение, подождите несколько секунд." : "Если картинка подвисает, выберите качество ниже. Эфир при этом не прерывается."}</p>
          </div>}
        </div>}
        <button className="iconbtn" onClick={() => { if (!diag) void api.liveDiagAccess(token).then((r) => setDiagMeeting(r.allowed ? r.meeting_id ?? null : null)).catch(() => undefined); setDiag(!diag); }} aria-label="Статистика воспроизведения" aria-pressed={diag} title="Статистика воспроизведения"><Icon name="gauge" size={22} /></button>
        <button className="iconbtn" onClick={fullscreen} aria-label="Во весь экран"><Icon name="expand" size={22} /></button>
      </div>
      {diag && <Suspense fallback={null}><LiveDiagPanel viewer={stats.current} meetingId={diagMeeting} onClose={() => setDiag(false)} /></Suspense>}
    </div>
  );
}

/** Что сказать зрителю о записи завершённого эфира (состояние даёт сервер: срок, закрытие доступа, подготовка и удаление проверяются там). */
const REPLAY_NOTE: Record<string, string> = {
  preparing: "Запись готовится — обычно несколько минут. Страница обновится сама.",
  slow: "Запись готовится дольше обычного. Откройте ссылку позже.",
  expired: "Срок просмотра записи по этой ссылке завершён.",
  revoked: "Организатор закрыл доступ к записи.",
  deleted: "Запись удалена и больше недоступна.",
  failed: "Запись этого эфира не удалось сохранить.",
};

export default function LivePage() {
  const { token = "" } = useParams();
  const [info, setInfo] = useState<LiveViewerInfo | null>(null);
  const [err, setErr] = useState("");
  const [access, setAccess] = useState<LiveAccess | null>(null);
  const [pwErr, setPwErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [skew, setSkew] = useState(0);
  const [, tick] = useState(0);
  const [replayUrl, setReplayUrl] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [askReplayPw, setAskReplayPw] = useState(false);
  const [replayErr, setReplayErr] = useState("");
  const pass = useRef<string>("");
  const infoRef = useRef<LiveViewerInfo | null>(null);
  const [viewer, setViewerState] = useState(() => ssGet(VIEWER_KEY(token)));
  const [rtcOff, setRtcOff] = useState(false);
  const viewerRef = useRef(viewer);
  const setViewer = useCallback((v: string) => { viewerRef.current = v; ssSet(VIEWER_KEY(token), v); setViewerState(v); }, [token]);
  const lostViewer = useCallback(() => setViewer(""), [setViewer]);

  useEffect(() => { try { pass.current = sessionStorage.getItem(SEEN_KEY(token)) ?? ""; } catch { pass.current = ""; } }, [token]);

  const load = useCallback(async () => {
    try {
      const i = await api.liveInfo(token);
      setInfo(i); infoRef.current = i; setErr("");
      setSkew(new Date(i.now).getTime() - Date.now());
      document.title = i.title;
    } catch (e) { setErr((e as ApiError).status === 404 ? "Трансляция не найдена: ссылка неверна или отозвана." : (e as ApiError).message || "Нет связи с сервером."); }
  }, [token]);

  const getAccess = useCallback(async (password?: string, avoid?: string, wantLow?: boolean): Promise<LiveAccess | null> => {
    try {
      const a = await api.liveAccess(token, { ...(password ? { password } : pass.current ? { pass: pass.current } : {}), long: native(), viewer: viewerRef.current, ...(avoid ? { avoid } : {}), ...(wantLow ? { want_low: true } : {}) });
      if (a.pass) { pass.current = a.pass; try { sessionStorage.setItem(SEEN_KEY(token), a.pass); } catch { /* пропуск не запомнится */ } }
      return a;
    } catch (e) {
      const x = e as ApiError;
      if (x.code === "password_required") { pass.current = ""; try { sessionStorage.removeItem(SEEN_KEY(token)); } catch { /* ок */ } void load(); }
      if (x.code === "viewer_required") setViewer("");
      if (password) setPwErr(x.status === 429 ? "Слишком много попыток. Повторите позже." : x.code === "password_invalid" ? "Неверный пароль." : x.message);
      return null;
    }
  }, [token, load, setViewer]);

  useEffect(() => { void load(); }, [load]);
  // страница ожидания опрашивает состояние чаще, идущий эфир — реже (конец эфира плеер увидит и сам)
  useEffect(() => {
    const t = window.setInterval(() => { void load(); }, info?.status === "live" ? 30_000 : 6_000);
    const c = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => { window.clearInterval(t); window.clearInterval(c); };
  }, [load, info?.status]);

  // доступ выдаём, как только он нужен: пароль не требуется или пропуск уже есть; иначе ждём ввода
  useEffect(() => {
    if (!info || access || !info.own_hls || !viewer) return;
    if (info.status !== "live") return;                          // плейлист готов только при «идёт»: раньше плеер упирался бы в 404
    if (info.password_required && !pass.current) return;
    void getAccess().then((a) => { if (a) setAccess(a); });
  }, [info, access, getAccess, viewer]);

  const submitPassword = async (pw: string) => {
    setBusy(true); setPwErr("");
    const a = await getAccess(pw);
    setBusy(false);
    if (a) setAccess(a);
  };
  const renew = useCallback(async (avoid?: string, wantLow?: boolean) => {
    const a = await getAccess(undefined, avoid, wantLow);
    if (!a) { setAccess(null); void load(); }
    return a;
  }, [getAccess, load]);

  const openReplay = async (password?: string) => {
    setBusy(true); setReplayErr("");
    try {
      const r = await api.liveReplay(token, { ...(password ? { password } : pass.current ? { pass: pass.current } : {}), viewer: viewerRef.current });
      if (r.pass) { pass.current = r.pass; try { sessionStorage.setItem(SEEN_KEY(token), r.pass); } catch { /* пропуск не запомнится */ } }
      setAskReplayPw(false); setReplayUrl(r.url); setDownloadUrl(r.download_url ?? "");
    } catch (e) {
      const x = e as ApiError;
      if (x.code === "viewer_required") setViewer("");
      else if (x.code === "password_required") setAskReplayPw(true);
      else setReplayErr(x.status === 429 ? "Слишком много попыток. Повторите позже." : x.code === "password_invalid" ? "Неверный пароль." : x.message || "Запись недоступна.");
    } finally { setBusy(false); }
  };

  if (err) return <section className="live-wrap"><div className="live-msg card"><Icon name="info" size={28} /><h1>{err}</h1><p className="muted">Попросите у организатора актуальную ссылку.</p></div></section>;
  if (!info) return <section className="live-wrap"><div className="muted">Загрузка…</div></section>;

  const now = Date.now() + skew;
  const startsMs = info.starts_at ? new Date(info.starts_at).getTime() - now : null;
  const needPw = info.password_required && !pass.current && !access;
  const showPlayer = !!access && info.status === "live" && info.own_hls;
  // представиться нужно перед ожиданием, эфиром и записью; страница завершённого эфира без записи открывается без этого
  const needViewer = !viewer && info.own_hls && (info.status !== "ended" || info.replay);
  if (needViewer) return (
    <section className="live-wrap">
      <header className="live-head"><h1>{info.title}</h1>{info.status === "live" && <span className="live-tag on big"><i aria-hidden />Прямой эфир</span>}</header>
      <div className="live-stage live-msg">
        <ViewerForm token={token} onDone={setViewer} title={info.status === "waiting" ? "Эфир скоро начнётся" : info.status === "ended" ? "Запись эфира" : "Прямой эфир"} />
      </div>
      {info.status === "waiting" && info.starts_at && <p className="live-when muted">Начало: {when(info.starts_at)}</p>}
    </section>
  );

  return (
    <section className={`live-wrap ${showPlayer && viewer ? "live-split" : ""}`}>
      <header className="live-head">
        <h1>{info.title}</h1>
        {info.status === "live" && <span className="live-tag on big"><i aria-hidden />Прямой эфир</span>}
        {info.status === "ended" && <span className="badge">Эфир завершён</span>}
      </header>
      {!info.available && <div className="alert warn">Трансляции отключены администратором сервера.</div>}
      {showPlayer && access && <div className="live-main">{access.rtc && !rtcOff
        ? <RtcView token={token} rtc={access.rtc} vid={access.vid} onFail={() => setRtcOff(true)} />
        : <Player token={token} access={access} renew={renew} skew={skew} onGone={() => { setAccess(null); void load(); }} />}</div>}
      {showPlayer && viewer && <ViewerQuestions token={token} viewer={viewer} pass={() => pass.current} onViewerLost={lostViewer} />}
      {!showPlayer && info.status === "live" && needPw && info.own_hls && (
        <div className="live-stage live-msg"><PasswordForm onSubmit={(pw) => void submitPassword(pw)} busy={busy} error={pwErr} /></div>)}
      {!showPlayer && info.status === "live" && !needPw && info.own_hls && (
        <div className="live-stage live-msg"><div className="live-spin" role="status" aria-label="Подключение" /><p>Подключаемся к эфиру…</p></div>)}
      {info.status === "starting" && info.own_hls && (
        <div className="live-stage live-msg"><div className="live-spin" role="status" aria-label="Запуск" /><h2>Эфир запускается</h2><p className="muted">Это займёт несколько секунд — страница обновится сама.</p></div>)}
      {!info.own_hls && (info.status === "live" || info.status === "starting") && (
        <div className="live-stage live-msg"><Icon name="broadcast" size={30} /><p>Эфир идёт на внешних площадках. Ссылку на просмотр даёт организатор.</p></div>)}
      {info.status === "waiting" && (
        <div className="live-stage live-msg">
          <Icon name="broadcast" size={30} />
          <h2>Эфир скоро начнётся</h2>
          {info.starts_at && <p className="live-when">Начало: {when(info.starts_at)}</p>}
          {startsMs !== null && startsMs > 0 && <p className="live-count" aria-live="off">{clock(startsMs)}</p>}
          {info.password_required && !pass.current && <p className="muted">Пароль потребуется, когда эфир начнётся.</p>}
          <p className="muted">Страница обновится сама, как только эфир начнётся.</p>
        </div>)}
      {info.status === "ended" && !replayUrl && (
        <div className="live-stage live-msg"><Icon name="check" size={30} /><h2>Эфир завершён</h2><p className="muted">Спасибо, что были с нами.{info.ended_at ? ` Окончание: ${when(info.ended_at)}.` : ""}</p>
          {(info.replay_state === "preparing" || info.replay_state === "slow") && <p className="live-note" role="status"><span className="live-spin small" aria-hidden />{REPLAY_NOTE[info.replay_state]}</p>}
          {info.replay_state && info.replay_state !== "ready" && info.replay_state !== "preparing" && info.replay_state !== "slow" && info.replay_state !== "off" && REPLAY_NOTE[info.replay_state] &&
            <p className="live-note">{REPLAY_NOTE[info.replay_state]}</p>}
          {info.replay && info.access_until && <p className="muted small">Запись доступна по этой ссылке до {until(info.access_until)}.</p>}
          {info.replay && !askReplayPw && <button className="btn primary" onClick={() => void openReplay()} disabled={busy}><Icon name="play" size={18} /> Смотреть запись</button>}
          {askReplayPw && <PasswordForm onSubmit={(pw) => void openReplay(pw)} busy={busy} error={replayErr} />}
          {!askReplayPw && replayErr && <div className="alert error" role="alert">{replayErr}</div>}</div>)}
      {info.status === "ended" && replayUrl && (
        <>
          <div className="live-stage"><video src={replayUrl} controls autoPlay playsInline aria-label="Запись эфира" /></div>
          {(downloadUrl || info.access_until) && (
            <div className="live-foot row">
              {info.access_until && <span className="muted small">Доступна до {until(info.access_until)}</span>}
              <div className="spacer" />
              {downloadUrl && <a className="btn" href={downloadUrl} download><Icon name="download" size={16} /> Скачать запись</a>}
            </div>)}
        </>)}
    </section>
  );
}
