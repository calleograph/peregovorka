import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { describeMediaError } from "../mediaErrors";
import { levelFromTimeDomain, toneWav, type PreJoin } from "../prejoin";
import { copyText } from "../util";
import { deniedText, HINT_DELAY_MS, planFor, type HintPlan } from "../permissionHint";

type Perm = PermissionState | "unknown";

/** Адрес настроек сайта в браузере пользователя (страницу браузера нельзя открыть из веб-страницы — показываем адрес, его можно скопировать). */
function settingsAddress(): { url: string; name: string } {
  const ua = navigator.userAgent;
  if (/YaBrowser/i.test(ua)) return { url: "browser://settings/content/microphone", name: "Яндекс.Браузер" };
  if (/Edg\//.test(ua)) return { url: "edge://settings/content/microphone", name: "Microsoft Edge" };
  if (/Firefox/i.test(ua)) return { url: "about:preferences#privacy", name: "Firefox" };
  return { url: "chrome://settings/content/microphone", name: "Chrome" };
}

async function queryPerm(name: "microphone" | "camera"): Promise<{ state: Perm; sub?: (cb: (s: Perm) => void) => () => void }> {
  try {
    const st = await navigator.permissions.query({ name: name as PermissionName });
    return { state: st.state, sub: (cb) => { const h = () => cb(st.state); st.addEventListener("change", h); return () => st.removeEventListener("change", h); } };
  } catch { return { state: "unknown" }; }          // браузер не умеет (Safari/Firefox для camera/microphone): узнаем по результату запроса
}

/** Подсказка поверх страницы, пока браузер ждёт ответа на системный запрос доступа: приглушает страницу и показывает, куда смотреть. Исчезает сама, когда запрос решён. */
function PermissionCallout({ plan }: { plan: HintPlan }) {
  return createPortal(
    <div className="perm-scrim">
      <div className={`perm-callout at-${plan.arrow}`} role="alert" aria-live="assertive">
        {plan.arrow !== "none" && (
          <svg className="perm-pointer" viewBox="0 0 48 48" width="76" height="76" aria-hidden>
            <path d="M34 40 L12 14 M12 14 L12 28 M12 14 L26 17" fill="none" stroke="currentColor" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
        <b>{plan.title}</b>
        <p>{plan.text}</p>
      </div>
    </div>, document.body);
}

type Kind = "audioinput" | "audiooutput" | "videoinput";
const NAMES: Record<Kind, string> = { audioinput: "Микрофон", audiooutput: "Динамики", videoinput: "Камера" };

/**
 * Проверка оборудования перед входом: выбор микрофона с живым индикатором уровня, проверка динамиков (короткий тон), выбор камеры с превью.
 * Ничего не отправляется на сервер; выбранные устройства передаются комнате. Вход возможен и без микрофона/камеры (с понятным пояснением).
 */
export default function PreJoinCheck({ cameraAllowed, onChange }: { cameraAllowed: boolean; onChange: (p: PreJoin & { micOk: boolean; micDenied: boolean }) => void }) {
  const [devices, setDevices] = useState<Record<Kind, MediaDeviceInfo[]>>({ audioinput: [], audiooutput: [], videoinput: [] });
  const [micId, setMicId] = useState("");
  const [speakerId, setSpeakerId] = useState("");
  const [camId, setCamId] = useState("");
  const [camOn, setCamOn] = useState(false);
  const [level, setLevel] = useState(0);
  const [micErr, setMicErr] = useState("");
  const [camErr, setCamErr] = useState("");
  const [micOk, setMicOk] = useState(false);
  const [asked, setAsked] = useState(false);
  const [perm, setPerm] = useState<{ mic: Perm; cam: Perm }>({ mic: "unknown", cam: "unknown" });
  const [copied, setCopied] = useState(false);
  const [toneBusy, setToneBusy] = useState(false);
  const micStream = useRef<MediaStream | null>(null);
  const camStream = useRef<MediaStream | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const raf = useRef(0);
  // Системный запрос браузера «висит», пока пользователь не ответит: считаем незавершённые обращения к устройствам; подсказка появляется, только если ответ не пришёл сразу
  const [pending, setPending] = useState(false);
  const inFlight = useRef(0);
  const hintTimer = useRef(0);
  const track = useCallback(async <T,>(fn: () => Promise<T>): Promise<T> => {
    inFlight.current += 1;
    if (!hintTimer.current) hintTimer.current = window.setTimeout(() => setPending(true), HINT_DELAY_MS);
    try { return await fn(); }
    finally {
      inFlight.current -= 1;
      if (inFlight.current <= 0) { inFlight.current = 0; window.clearTimeout(hintTimer.current); hintTimer.current = 0; setPending(false); }
    }
  }, []);
  useEffect(() => () => window.clearTimeout(hintTimer.current), []);

  const listDevices = useCallback(async () => {
    const all = await navigator.mediaDevices.enumerateDevices().catch(() => [] as MediaDeviceInfo[]);
    setDevices({ audioinput: all.filter((d) => d.kind === "audioinput"), audiooutput: all.filter((d) => d.kind === "audiooutput"), videoinput: all.filter((d) => d.kind === "videoinput") });
  }, []);

  const stopMic = useCallback(() => {
    cancelAnimationFrame(raf.current);
    micStream.current?.getTracks().forEach((t) => t.stop()); micStream.current = null;
    void ctxRef.current?.close().catch(() => undefined); ctxRef.current = null;
    setLevel(0);
  }, []);

  const startMic = useCallback(async (id?: string) => {
    stopMic();
    setMicErr("");
    try {
      const stream = await track(() => navigator.mediaDevices.getUserMedia({ audio: id ? { deviceId: { exact: id } } : true }));
      micStream.current = stream;
      setMicOk(true);
      const used = stream.getAudioTracks()[0]?.getSettings().deviceId;
      if (used && !id) setMicId(used);
      const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AC(); ctxRef.current = ctx;
      const an = ctx.createAnalyser(); an.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(an);
      const data = new Uint8Array(an.fftSize);
      const tick = () => { an.getByteTimeDomainData(data); setLevel(levelFromTimeDomain(data)); raf.current = requestAnimationFrame(tick); };
      tick();
      await listDevices(); // подписи устройств доступны только после разрешения
    } catch (e) {
      setMicOk(false);
      setMicErr(describeMediaError(e, "mic").message);
    }
  }, [listDevices, stopMic, track]);

  const startCam = useCallback(async (id?: string) => {
    camStream.current?.getTracks().forEach((t) => t.stop()); camStream.current = null;
    setCamErr("");
    try {
      const stream = await track(() => navigator.mediaDevices.getUserMedia({ video: id ? { deviceId: { exact: id } } : true }));
      camStream.current = stream;
      const used = stream.getVideoTracks()[0]?.getSettings().deviceId;
      if (used && !id) setCamId(used);
      setCamOn(true);
      await listDevices();
    } catch (e) { setCamOn(false); setCamErr(describeMediaError(e, "camera").message); }
  }, [listDevices, track]);

  const stopCam = useCallback(() => { camStream.current?.getTracks().forEach((t) => t.stop()); camStream.current = null; setCamOn(false); }, []);

  // Состояние разрешений браузера: «запрещено» — повторно getUserMedia не вызываем (браузер всё равно не покажет запрос), даём инструкцию
  useEffect(() => {
    let alive = true; const offs: (() => void)[] = [];
    void (async () => {
      for (const [k, name] of [["mic", "microphone"], ["cam", "camera"]] as const) {
        const r = await queryPerm(name);
        if (!alive) return;
        setPerm((p) => ({ ...p, [k]: r.state }));
        const off = r.sub?.((s) => setPerm((p) => ({ ...p, [k]: s })));
        if (off) offs.push(off);
      }
    })();
    return () => { alive = false; offs.forEach((f) => f()); };
  }, []);

  /** Один клик «Запросить разрешения»: микрофон (проверка уровня) и, если комната допускает камеру, доступ к камере (камера при этом не включается). */
  const requestAll = async () => {
    setAsked(true);
    if (perm.mic !== "denied") await startMic(micId || undefined);
    if (cameraAllowed && perm.cam !== "denied" && !camOn) {
      try { const s = await track(() => navigator.mediaDevices.getUserMedia({ video: true })); s.getTracks().forEach((t) => t.stop()); setCamErr(""); await listDevices(); }
      catch (e) { setCamErr(describeMediaError(e, "camera").message); }
    }
  };
  const denied = perm.mic === "denied" || (cameraAllowed && perm.cam === "denied");
  const plan = planFor(navigator.userAgent);
  /** «Проверить снова»: прочитать состояние разрешений заново (после того как пользователь поправил их у адресной строки) и повторить запрос, если он больше не запрещён. */
  const recheck = async () => {
    for (const [k, name] of [["mic", "microphone"], ["cam", "camera"]] as const) { const r = await queryPerm(name); setPerm((p) => ({ ...p, [k]: r.state })); }
    setMicErr(""); setCamErr("");
    await listDevices();
    await requestAll();
  };

  useEffect(() => { void listDevices(); return () => { stopMic(); camStream.current?.getTracks().forEach((t) => t.stop()); }; }, [listDevices, stopMic]);
  useEffect(() => { if (video.current) video.current.srcObject = camOn ? camStream.current : null; }, [camOn, camId]);
  useEffect(() => { onChange({ micId: micId || undefined, speakerId: speakerId || undefined, camId: camId || undefined, camOn, micOk, micDenied: perm.mic === "denied" }); }, [micId, speakerId, camId, camOn, micOk, perm.mic, onChange]);

  const playTone = async () => {
    setToneBusy(true);
    try {
      const url = URL.createObjectURL(new Blob([toneWav() as BlobPart], { type: "audio/wav" }));
      const a = new Audio(url);
      const sink = (a as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }).setSinkId;
      if (speakerId && sink) await sink.call(a, speakerId).catch(() => undefined);
      a.onended = () => { URL.revokeObjectURL(url); setToneBusy(false); };
      await a.play();
    } catch { setToneBusy(false); }
  };

  const canSink = "setSinkId" in HTMLMediaElement.prototype;
  const bars = 16;
  const micState = perm.mic === "denied" ? "запрещён" : micOk || perm.mic === "granted" ? "разрешён" : "не разрешён";
  const camState = perm.cam === "denied" ? "запрещена" : camOn || perm.cam === "granted" ? "разрешена" : "не разрешена";
  return (
    <div className="precheck" role="group" aria-label="Проверка оборудования — по желанию">
      {denied && (
        <div className="alert error small pc-wide" role="alert">
          <b>{deniedText(perm.mic === "denied", cameraAllowed && perm.cam === "denied")}</b>
          <div className="pc-btns" style={{ marginTop: 6 }}>
            <button type="button" className="btn mini primary" onClick={() => void recheck()}>Проверить снова</button>
            <button type="button" className="btn mini" onClick={async () => { setCopied(await copyText(settingsAddress().url)); }}>{copied ? "Скопировано — вставьте в адресную строку" : `Скопировать адрес настроек (${settingsAddress().name})`}</button>
          </div>
          {perm.mic === "denied"
            ? <div className="perm-nomic"><b>Без микрофона вы войдёте в комнату, но не сможете говорить.</b> Остальные вас не услышат.</div>
            : <div>Камера отдельно: микрофон работает, войти можно и без видео.</div>}
        </div>
      )}
      <div className="pc-card pc-perm">
        <div className="pc-h"><b>Разрешения</b><span className="pc-note">по желанию</span></div>
        <p className="pc-p">Разрешите доступ к микрофону{cameraAllowed ? " и камере" : ""}, чтобы проверить оборудование.</p>
        <div className="pc-pills">
          <span className={`pill ${perm.mic === "granted" || micOk ? "live" : ""}`}>Микрофон: {micState}</span>
          {cameraAllowed && <span className={`pill ${perm.cam === "granted" || camOn ? "live" : ""}`}>Камера: {camState}</span>}
        </div>
        <div className="pc-btns">
          {perm.mic !== "denied" && <button type="button" className="btn mini primary" onClick={() => void requestAll()}>Разрешить доступ</button>}
        </div>
      </div>

      <div className="pc-card">
        <div className="pc-h"><b>{NAMES.audioinput}</b>{micOk && <span className="badge ok">работает</span>}</div>
        <select aria-label={NAMES.audioinput} value={micId} onChange={(e) => { setMicId(e.target.value); void startMic(e.target.value); }} disabled={!devices.audioinput.length}>
          {!devices.audioinput.length && <option value="">Микрофон не найден</option>}
          {devices.audioinput.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${NAMES.audioinput} ${i + 1}`}</option>)}
        </select>
        <div className="level" role="meter" aria-label="Уровень микрофона" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(level * 100)}>
          {Array.from({ length: bars }, (_, i) => <span key={i} className={i < Math.round(level * bars) ? (i > bars * 0.8 ? "hot" : "on") : ""} />)}
        </div>
        <div className="pc-btns">
          {!asked || !micOk
            ? <button type="button" className="btn mini" disabled={perm.mic === "denied"} onClick={() => { setAsked(true); void startMic(micId || undefined); }}>Проверить микрофон</button>
            : <span className="pc-hint">Скажите что-нибудь — полоса должна двигаться.</span>}
        </div>
        {micErr && <div className="alert error small" role="alert">{micErr} Войти можно и без микрофона.</div>}
      </div>

      {cameraAllowed && (
        <div className="pc-card">
          <div className="pc-h"><b>{NAMES.videoinput}</b>{camOn && <span className="badge ok">включена</span>}</div>
          <select aria-label={NAMES.videoinput} value={camId} onChange={(e) => { setCamId(e.target.value); if (camOn) void startCam(e.target.value); }} disabled={!devices.videoinput.length}>
            {!devices.videoinput.length && <option value="">Камера не обнаружена</option>}
            {devices.videoinput.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${NAMES.videoinput} ${i + 1}`}</option>)}
          </select>
          {camOn && <video ref={video} className="precheck-video" autoPlay playsInline muted />}
          <div className="pc-btns">
            {camOn
              ? <button type="button" className="btn mini" onClick={stopCam}>Выключить камеру</button>
              : <button type="button" className="btn mini" disabled={!devices.videoinput.length} onClick={() => void startCam(camId || undefined)}>Проверить камеру</button>}
            {!devices.videoinput.length && <span className="pc-hint">Камера не обнаружена. Войти можно без видео.</span>}
          </div>
          {camErr && <div className="alert error small" role="alert">{camErr}</div>}
        </div>
      )}

      <div className="pc-card">
        <div className="pc-h"><b>{NAMES.audiooutput}</b></div>
        <select aria-label={NAMES.audiooutput} value={speakerId} onChange={(e) => setSpeakerId(e.target.value)} disabled={!canSink || !devices.audiooutput.length}>
          {(!canSink || !devices.audiooutput.length) && <option value="">По умолчанию</option>}
          {canSink && devices.audiooutput.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${NAMES.audiooutput} ${i + 1}`}</option>)}
        </select>
        <div className="pc-btns">
          <button type="button" className="btn mini" onClick={() => void playTone()} disabled={toneBusy}>{toneBusy ? "Играет…" : "Проверить динамики"}</button>
          {!canSink && <span className="pc-hint">Звук пойдёт на устройство по умолчанию.</span>}
        </div>
      </div>

      {pending && <PermissionCallout plan={plan} />}
    </div>
  );
}
