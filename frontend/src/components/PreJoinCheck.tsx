import { useCallback, useEffect, useRef, useState } from "react";
import { describeMediaError } from "../mediaErrors";
import { levelFromTimeDomain, toneWav, type PreJoin } from "../prejoin";

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
      <div className="pc-row">
        <label>{NAMES.audioinput}
          <select value={micId} onChange={(e) => { setMicId(e.target.value); void startMic(e.target.value); }} disabled={!devices.audioinput.length}>
            {!devices.audioinput.length && <option value="">— не найден —</option>}
            {devices.audioinput.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${NAMES.audioinput} ${i + 1}`}</option>)}
          </select>
        </label>
        {!asked || !micOk
          ? <button type="button" className="btn mini primary" onClick={() => { setAsked(true); void startMic(micId || undefined); }}>Проверить микрофон</button>
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
