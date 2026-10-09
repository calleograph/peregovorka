import { useCallback, useEffect, useRef, useState } from "react";
import { describeMediaError } from "../mediaErrors";
import { levelFromTimeDomain, toneWav, type PreJoin } from "../prejoin";
import { copyText } from "../util";

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

type Kind = "audioinput" | "audiooutput" | "videoinput";
const NAMES: Record<Kind, string> = { audioinput: "Микрофон", audiooutput: "Динамики", videoinput: "Камера" };

/**
 * Проверка оборудования перед входом: выбор микрофона с живым индикатором уровня, проверка динамиков (короткий тон), выбор камеры с превью.
 * Ничего не отправляется на сервер; выбранные устройства передаются комнате. Вход возможен и без микрофона/камеры (с понятным пояснением).
 */
export default function PreJoinCheck({ cameraAllowed, onChange }: { cameraAllowed: boolean; onChange: (p: PreJoin & { micOk: boolean }) => void }) {
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
      const stream = await navigator.mediaDevices.getUserMedia({ audio: id ? { deviceId: { exact: id } } : true });
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
  }, [listDevices, stopMic]);

  const startCam = useCallback(async (id?: string) => {
    camStream.current?.getTracks().forEach((t) => t.stop()); camStream.current = null;
    setCamErr("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: id ? { deviceId: { exact: id } } : true });
      camStream.current = stream;
      const used = stream.getVideoTracks()[0]?.getSettings().deviceId;
      if (used && !id) setCamId(used);
      setCamOn(true);
      await listDevices();
    } catch (e) { setCamOn(false); setCamErr(describeMediaError(e, "camera").message); }
  }, [listDevices]);

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
      try { const s = await navigator.mediaDevices.getUserMedia({ video: true }); s.getTracks().forEach((t) => t.stop()); setCamErr(""); await listDevices(); }
      catch (e) { setCamErr(describeMediaError(e, "camera").message); }
    }
  };
  const denied = perm.mic === "denied" || (cameraAllowed && perm.cam === "denied");
  const waiting = asked && !micOk && !micErr && perm.mic !== "denied";

  useEffect(() => { void listDevices(); return () => { stopMic(); camStream.current?.getTracks().forEach((t) => t.stop()); }; }, [listDevices, stopMic]);
  useEffect(() => { if (video.current) video.current.srcObject = camOn ? camStream.current : null; }, [camOn, camId]);
  useEffect(() => { onChange({ micId: micId || undefined, speakerId: speakerId || undefined, camId: camId || undefined, camOn, micOk }); }, [micId, speakerId, camId, camOn, micOk, onChange]);

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
  return (
    <fieldset className="precheck">
      <legend>Проверка оборудования</legend>
      <div className="perm-intro">
        <p style={{ margin: 0 }}>Сейчас браузер запросит доступ к микрофону{cameraAllowed ? " и камере" : ""}. <b>Рекомендуем разрешить доступ сразу</b> — иначе позже браузер может не показать запрос повторно.</p>
        <div className="row">
          {perm.mic !== "denied" && <button type="button" className="btn mini primary" onClick={() => void requestAll()}>Запросить разрешения</button>}
          <span className={`pill ${perm.mic === "granted" || micOk ? "live" : ""}`}>Микрофон: {perm.mic === "denied" ? "запрещён" : micOk || perm.mic === "granted" ? "разрешён" : "ещё не разрешён"}</span>
          {cameraAllowed && <span className={`pill ${perm.cam === "granted" || camOn ? "live" : ""}`}>Камера: {perm.cam === "denied" ? "запрещена" : camOn || perm.cam === "granted" ? "разрешена" : "ещё не разрешена"}</span>}
          {waiting && <span className="perm-arrow" aria-hidden title="Окно запроса браузера — слева вверху, у адресной строки">↖ окно браузера</span>}
        </div>
        {denied && (
          <div className="alert error small" role="alert">
            <b>Доступ запрещён в настройках браузера</b>, поэтому запрос больше не появится. Нажмите значок настроек сайта слева от адреса (замок), включите {perm.mic === "denied" ? "микрофон" : ""}{perm.mic === "denied" && cameraAllowed && perm.cam === "denied" ? " и " : ""}{cameraAllowed && perm.cam === "denied" ? "камеру" : ""} и обновите страницу. Или откройте настройки сайтов: <code>{settingsAddress().url}</code> ({settingsAddress().name}){" "}
            <button type="button" className="btn mini" onClick={async () => { setCopied(await copyText(settingsAddress().url)); }}>{copied ? "Скопировано — вставьте в адресную строку" : "Скопировать адрес"}</button>
            <div>Войти в комнату можно и без {perm.mic === "denied" ? "микрофона" : "камеры"} — включить позже можно будет, разрешив доступ.</div>
          </div>
        )}
      </div>
      <div className="pc-row">
        <label>{NAMES.audioinput}
          <select value={micId} onChange={(e) => { setMicId(e.target.value); void startMic(e.target.value); }} disabled={!devices.audioinput.length}>
            {!devices.audioinput.length && <option value="">— не найден —</option>}
            {devices.audioinput.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${NAMES.audioinput} ${i + 1}`}</option>)}
          </select>
        </label>
        {!asked || !micOk
          ? <button type="button" className="btn mini" disabled={perm.mic === "denied"} onClick={() => { setAsked(true); void startMic(micId || undefined); }}>Проверить микрофон</button>
          : <span className="badge ok">Микрофон работает</span>}
      </div>
      <div className="level" role="meter" aria-label="Уровень микрофона" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(level * 100)}>
        {Array.from({ length: bars }, (_, i) => <span key={i} className={i < Math.round(level * bars) ? (i > bars * 0.8 ? "hot" : "on") : ""} />)}
      </div>
      <p className="muted small">{micOk ? "Скажите что-нибудь — полоса должна двигаться." : "Нажмите «Проверить микрофон» и разрешите доступ в браузере."}</p>
      {micErr && <div className="alert error small" role="alert">{micErr} Войти в комнату можно и без микрофона — включить его можно позже.</div>}

      <div className="pc-row">
        <label>{NAMES.audiooutput}
          <select value={speakerId} onChange={(e) => setSpeakerId(e.target.value)} disabled={!canSink || !devices.audiooutput.length}>
            {(!canSink || !devices.audiooutput.length) && <option value="">По умолчанию</option>}
            {canSink && devices.audiooutput.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${NAMES.audiooutput} ${i + 1}`}</option>)}
          </select>
        </label>
        <button type="button" className="btn mini" onClick={() => void playTone()} disabled={toneBusy}>{toneBusy ? "Играет…" : "Проверить динамики"}</button>
      </div>
      {!canSink && <p className="muted small">Этот браузер не позволяет выбрать динамики — звук пойдёт на устройство по умолчанию.</p>}

      {cameraAllowed && (
        <>
          <div className="pc-row">
            <label>{NAMES.videoinput}
              <select value={camId} onChange={(e) => { setCamId(e.target.value); if (camOn) void startCam(e.target.value); }} disabled={!devices.videoinput.length}>
                {!devices.videoinput.length && <option value="">— не найдена —</option>}
                {devices.videoinput.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${NAMES.videoinput} ${i + 1}`}</option>)}
              </select>
            </label>
            {camOn
              ? <button type="button" className="btn mini" onClick={stopCam}>Выключить камеру</button>
              : <button type="button" className="btn mini" onClick={() => void startCam(camId || undefined)}>Проверить камеру</button>}
          </div>
          {camOn && <video ref={video} className="precheck-video" autoPlay playsInline muted />}
          {camErr && <div className="alert error small" role="alert">{camErr}</div>}
          {camOn && <p className="muted small">Камера будет включена при входе. Если не нужна — выключите её здесь.</p>}
        </>
      )}
    </fieldset>
  );
}
