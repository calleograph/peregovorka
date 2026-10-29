import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type ApiError, type LiveAccess, type LiveViewerInfo } from "../api";
import { Icon } from "../components/Icons";
import "../live.css";

/**
 * Страница зрителя прямого эфира /live/<токен>: без входа в систему, без подключения к LiveKit. Зритель получает от приложения только короткую подписанную ссылку
 * на плейлист HLS (при включённом пароле — после его проверки), а видео отдаёт nginx напрямую. Подпись продлевается незаметно: плеер подменяет в адресах запросов
 * сегментов только подписанную часть пути, не перезапуская воспроизведение.
 */
const SEEN_KEY = (t: string) => `pg:live:${t}`;
const HLS_PREFIX = /^(?:https?:\/\/[^/]+)?\/hls\/[0-9a-f]{24}\/\d+\/[A-Za-z0-9_-]{22}\//;       // адрес с узлом (CDN) или без него

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

/** Плеер: нативный HLS (Safari, iPhone, iPad) или hls.js (остальные). Свои кнопки: пауза, звук, возврат к эфиру, полный экран. */
function Player({ token, access, renew, onGone }: { token: string; access: LiveAccess; renew: () => Promise<LiveAccess | null>; onGone: () => void }) {
  const wrap = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const prefix = useRef<string>(access.path.replace(/[^/]+$/, ""));
  const baseRef = useRef<string>(access.base ?? "");                 // CDN; при его недоступности плеер сам переходит на сервер-источник (пусто)
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
  const hlsRef = useRef<{ destroy: () => void; recoverMediaError: () => void; startLoad: () => void; liveSyncPosition?: number | null } | null>(null);

  // подпись продлевается заранее; адреса сегментов получают свежий префикс без перезапуска плеера
  useEffect(() => {
    let alive = true;
    let timer = window.setTimeout(function tick() {
      void renew().then((a) => {
        if (!alive || !a) return;
        prefix.current = a.path.replace(/[^/]+$/, "");
        const el = video.current;
        if (el && native()) { const was = !el.paused; el.src = baseRef.current + a.path; if (was) void el.play().catch(() => undefined); }        // нативный HLS подписи не подменяет: источник меняется (редко: подпись для Safari живёт часы)
      }).finally(() => { if (alive) timer = window.setTimeout(tick, Math.max(60, access.refresh_in) * 1000); });
    }, Math.max(60, access.refresh_in) * 1000);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [access.refresh_in, renew]);

  // счётчик зрителей: «пульс» раз в полминуты, пока вкладка видима
  useEffect(() => {
    const ping = () => { if (document.visibilityState === "visible") void api.livePing(token, access.vid).catch(() => undefined); };
    ping();
    const t = window.setInterval(ping, 30_000);
    return () => window.clearInterval(t);
  }, [token, access.vid]);

  useEffect(() => {
    const el = video.current;
    if (!el) return;
    let cancelled = false;
    const url = baseRef.current + access.path;
    el.muted = false;
    el.dataset.engine = native() ? "native" : "hlsjs";           // для проверок: какой механизм воспроизведения выбран
    const start = () => el.play().catch(() => { el.muted = true; setMuted(true); setNeedSound(true); return el.play().catch(() => undefined); });
    if (native()) {
      el.src = url;
      el.addEventListener("loadedmetadata", () => void start(), { once: true });
      el.addEventListener("error", () => {
        fails.current += 1;
        if (baseRef.current && fails.current >= 2) { baseRef.current = ""; fails.current = 0; el.src = access.path; void start(); return; }         // CDN недоступна: источник
        if (fails.current > 5) { setFatal("Не удаётся получить поток эфира. Обновите страницу или попробуйте позже."); return; }
        window.setTimeout(() => void renew().then((a) => { if (!a) return onGone(); prefix.current = a.path.replace(/[^/]+$/, ""); el.src = baseRef.current + a.path; void start(); }), Math.min(15_000, 1000 * 2 ** (fails.current - 1)));
      });
    } else {
      void import("hls.js").then(({ default: Hls }) => {
        if (cancelled) return;
        if (!Hls.isSupported()) { setFatal("Этот браузер не умеет воспроизводить прямой эфир. Попробуйте другой браузер."); return; }
        const hls = new Hls({
          enableWorker: false, lowLatencyMode: false, liveSyncDurationCount: 3, liveMaxLatencyDurationCount: 7, maxBufferLength: 20, backBufferLength: 20,
          manifestLoadPolicy: { default: { maxTimeToFirstByteMs: 10000, maxLoadTimeMs: 20000, timeoutRetry: { maxNumRetry: 4, retryDelayMs: 1000, maxRetryDelayMs: 4000 }, errorRetry: { maxNumRetry: 6, retryDelayMs: 1000, maxRetryDelayMs: 4000 } } },
          xhrSetup: (xhr: XMLHttpRequest, u: string) => { xhr.open("GET", u.replace(HLS_PREFIX, baseRef.current + prefix.current), true); },
        });
        hlsRef.current = hls as never;
        hls.loadSource(url);
        hls.attachMedia(el);
        hls.on(Hls.Events.MANIFEST_PARSED, () => void start());
        hls.on(Hls.Events.ERROR, (_e, d) => {
          if (!d.fatal) return;
          const code = (d as { response?: { code?: number } }).response?.code;
          if (d.type === Hls.ErrorTypes.NETWORK_ERROR) {
            // повторы с нарастающей паузой и пределом: сломанный адрес не должен превращаться в шквал запросов к приложению
            if (baseRef.current && (!code || code >= 500) && fails.current >= 1) {
              // CDN недоступна (нет ответа или 5xx): те же подписанные адреса берём прямо с сервера-источника, зритель этого почти не заметит
              baseRef.current = "";
              fails.current = 0;
              hls.loadSource(access.path);
              return;
            }
            fails.current += 1;
            if (fails.current > 6) { setFatal("Не удаётся получить поток эфира. Обновите страницу или попробуйте позже."); return; }
            const wait = Math.min(15_000, 1000 * 2 ** (fails.current - 1));
            if (code === 404 || code === 403 || code === 410) { window.setTimeout(() => void renew().then((a) => { if (!a) onGone(); else { prefix.current = a.path.replace(/[^/]+$/, ""); hls.startLoad(); } }), wait); }
            else window.setTimeout(() => hls.startLoad(), wait);
          } else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
          else setFatal("Не удалось воспроизвести эфир.");
        });
      }).catch(() => setFatal("Не удалось загрузить плеер."));
    }
    return () => { cancelled = true; hlsRef.current?.destroy(); hlsRef.current = null; el.removeAttribute("src"); el.load(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access.path.split("/")[2]]);          // источник пересоздаётся только при смене идентификатора доступа, а не при продлении подписи

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
  const toggle = () => { const el = video.current; if (!el) return; if (el.paused) void el.play(); else el.pause(); poke(); };
  const goLive = () => { const el = video.current; if (!el) return; const end = el.seekable.length ? el.seekable.end(el.seekable.length - 1) : 0; if (end) el.currentTime = end - 1; void el.play(); poke(); };
  const unmute = () => { const el = video.current; if (!el) return; el.muted = false; el.volume = el.volume || 1; setNeedSound(false); void el.play(); };
  const fullscreen = () => {
    const w = wrap.current as (HTMLDivElement & { webkitRequestFullscreen?: () => void }) | null, el = video.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
    if (document.fullscreenElement) { void document.exitFullscreen(); return; }
    if (w?.requestFullscreen) void w.requestFullscreen().catch(() => undefined);
    else if (el?.webkitEnterFullscreen) el.webkitEnterFullscreen();            // iPhone: полноэкранный режим только у самого видео
    else if (w?.webkitRequestFullscreen) w.webkitRequestFullscreen();
  };

  if (fatal) return <div className="live-stage live-msg"><Icon name="info" size={26} /><p>{fatal}</p></div>;
  return (
    <div className={`live-stage ${ui || paused ? "ui" : ""}`} ref={wrap} onMouseMove={poke} onTouchStart={poke}>
      <video ref={video} playsInline autoPlay aria-label="Прямой эфир" onClick={toggle} />
      {buffering && <div className="live-spin" role="status" aria-label="Загрузка" />}
      {needSound && <button className="live-sound btn primary" onClick={unmute}><Icon name="volumeOff" size={18} /> Включить звук</button>}
      <div className="live-bar" role="group" aria-label="Управление просмотром">
        <button className="iconbtn" onClick={toggle} aria-label={paused ? "Смотреть" : "Пауза"}><Icon name={paused ? "play" : "pause"} size={22} /></button>
        <button className="iconbtn" onClick={() => { const el = video.current; if (el) { el.muted = !el.muted; if (!el.muted && el.volume === 0) el.volume = 1; } }} aria-label={muted ? "Включить звук" : "Выключить звук"}><Icon name={muted ? "volumeOff" : "volume"} size={22} /></button>
        <input className="live-vol" type="range" min={0} max={1} step={0.05} value={muted ? 0 : vol} aria-label="Громкость" onChange={(e) => { const el = video.current; if (el) { el.volume = Number(e.target.value); el.muted = el.volume === 0; } }} />
        <span className={`live-tag ${behind > 8 ? "late" : "on"}`} title={behind > 8 ? `Вы отстаёте от эфира на ${Math.round(behind)} с` : "Вы смотрите прямой эфир"}><i aria-hidden />{behind > 8 ? `−${Math.round(behind)} с` : "ЭФИР"}</span>
        {behind > 8 && <button className="btn mini" onClick={goLive}>К прямому эфиру</button>}
        <span className="spacer" />
        <button className="iconbtn" onClick={fullscreen} aria-label="Во весь экран"><Icon name="expand" size={22} /></button>
      </div>
    </div>
  );
}

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
  const [askReplayPw, setAskReplayPw] = useState(false);
  const [replayErr, setReplayErr] = useState("");
  const pass = useRef<string>("");
  const infoRef = useRef<LiveViewerInfo | null>(null);

  useEffect(() => { try { pass.current = sessionStorage.getItem(SEEN_KEY(token)) ?? ""; } catch { pass.current = ""; } }, [token]);

  const load = useCallback(async () => {
    try {
      const i = await api.liveInfo(token);
      setInfo(i); infoRef.current = i; setErr("");
      setSkew(new Date(i.now).getTime() - Date.now());
      document.title = i.title;
    } catch (e) { setErr((e as ApiError).status === 404 ? "Трансляция не найдена: ссылка неверна или отозвана." : (e as ApiError).message || "Нет связи с сервером."); }
  }, [token]);

  const getAccess = useCallback(async (password?: string): Promise<LiveAccess | null> => {
    try {
      const a = await api.liveAccess(token, { ...(password ? { password } : pass.current ? { pass: pass.current } : {}), long: native() });
      if (a.pass) { pass.current = a.pass; try { sessionStorage.setItem(SEEN_KEY(token), a.pass); } catch { /* пропуск не запомнится */ } }
      return a;
    } catch (e) {
      const x = e as ApiError;
      if (x.code === "password_required") { pass.current = ""; try { sessionStorage.removeItem(SEEN_KEY(token)); } catch { /* ок */ } void load(); }
      if (password) setPwErr(x.status === 429 ? "Слишком много попыток. Повторите позже." : x.code === "password_invalid" ? "Неверный пароль." : x.message);
      return null;
    }
  }, [token, load]);

  useEffect(() => { void load(); }, [load]);
  // страница ожидания опрашивает состояние чаще, идущий эфир — реже (конец эфира плеер увидит и сам)
  useEffect(() => {
    const t = window.setInterval(() => { void load(); }, info?.status === "live" ? 30_000 : 6_000);
    const c = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => { window.clearInterval(t); window.clearInterval(c); };
  }, [load, info?.status]);

  // доступ выдаём, как только он нужен: пароль не требуется или пропуск уже есть; иначе ждём ввода
  useEffect(() => {
    if (!info || access || !info.own_hls) return;
    if (info.status !== "live") return;                          // плейлист готов только при «идёт»: раньше плеер упирался бы в 404
    if (info.password_required && !pass.current) return;
    void getAccess().then((a) => { if (a) setAccess(a); });
  }, [info, access, getAccess]);

  const submitPassword = async (pw: string) => {
    setBusy(true); setPwErr("");
    const a = await getAccess(pw);
    setBusy(false);
    if (a) setAccess(a);
  };
  const renew = useCallback(async () => {
    const a = await getAccess();
    if (!a) { setAccess(null); void load(); }
    return a;
  }, [getAccess, load]);

  const openReplay = async (password?: string) => {
    setBusy(true); setReplayErr("");
    try {
      const r = await api.liveReplay(token, password ? { password } : pass.current ? { pass: pass.current } : {});
      if (r.pass) { pass.current = r.pass; try { sessionStorage.setItem(SEEN_KEY(token), r.pass); } catch { /* пропуск не запомнится */ } }
      setAskReplayPw(false); setReplayUrl(r.url);
    } catch (e) {
      const x = e as ApiError;
      if (x.code === "password_required") setAskReplayPw(true);
      else setReplayErr(x.status === 429 ? "Слишком много попыток. Повторите позже." : x.code === "password_invalid" ? "Неверный пароль." : x.message || "Запись недоступна.");
    } finally { setBusy(false); }
  };

  if (err) return <section className="live-wrap"><div className="live-msg card"><Icon name="info" size={28} /><h1>{err}</h1><p className="muted">Попросите у организатора актуальную ссылку.</p></div></section>;
  if (!info) return <section className="live-wrap"><div className="muted">Загрузка…</div></section>;

  const now = Date.now() + skew;
  const startsMs = info.starts_at ? new Date(info.starts_at).getTime() - now : null;
  const needPw = info.password_required && !pass.current && !access;
  const showPlayer = !!access && info.status === "live" && info.own_hls;

  return (
    <section className="live-wrap">
      <header className="live-head">
        <h1>{info.title}</h1>
        {info.status === "live" && <span className="live-tag on big"><i aria-hidden />Прямой эфир</span>}
        {info.status === "ended" && <span className="badge">Эфир завершён</span>}
      </header>
      {!info.available && <div className="alert warn">Трансляции отключены администратором сервера.</div>}
      {showPlayer && access && <Player token={token} access={access} renew={renew} onGone={() => { setAccess(null); void load(); }} />}
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
          <Icon name="clock" size={30} />
          <h2>Эфир ещё не начался</h2>
          {info.starts_at && <p className="live-when">Начало: {when(info.starts_at)}</p>}
          {startsMs !== null && startsMs > 0 && <p className="live-count" aria-live="off">{clock(startsMs)}</p>}
          {info.password_required && !pass.current && <p className="muted">Пароль потребуется, когда эфир начнётся.</p>}
          <p className="muted">Страница обновится сама, как только эфир начнётся.</p>
        </div>)}
      {info.status === "ended" && !replayUrl && (
        <div className="live-stage live-msg"><Icon name="check" size={30} /><h2>Эфир завершён</h2><p className="muted">Спасибо, что были с нами.{info.ended_at ? ` Закончился ${when(info.ended_at)}.` : ""}</p>
          {info.replay && !askReplayPw && <button className="btn primary" onClick={() => void openReplay()} disabled={busy}><Icon name="play" size={18} /> Смотреть запись</button>}
          {askReplayPw && <PasswordForm onSubmit={(pw) => void openReplay(pw)} busy={busy} error={replayErr} />}
          {!askReplayPw && replayErr && <div className="alert error" role="alert">{replayErr}</div>}</div>)}
      {info.status === "ended" && replayUrl && (
        <div className="live-stage"><video src={replayUrl} controls autoPlay playsInline aria-label="Запись эфира" /></div>)}
    </section>
  );
}
