import { ScreenSharePresets, type LocalVideoTrack, type ScreenShareCaptureOptions, type TrackPublishOptions } from "livekit-client";

export type ScreenProfile = "sharp" | "balanced" | "motion" | "eco";

export const PROFILE_LABELS: Record<ScreenProfile, string> = {
  sharp: "Чёткость (текст, слайды, код)",
  balanced: "Сбалансированный",
  motion: "Плавность (видео, анимация)",
  eco: "Экономный 720p (слайды, мало трафика)",
};

/** «Экономный 720p»: 1280×720, 10 к/с, цель 800 кбит/с (допустимо 500–900). Предназначен для презентаций: читаемость слайдов и экономия трафика. */
export const ECO = { width: 1280, height: 720, fps: 10, kbps: 800, minKbps: 500, maxKbps: 900 } as const;

export const clampEcoKbps = (v: unknown): number => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.min(ECO.maxKbps, Math.max(ECO.minKbps, n)) : ECO.kbps;
};

/**
 * Параметры «быстрой и качественной» трансляции экрана одним кликом.
 *  - захват до 1080p; contentHint подсказывает кодеку, что важнее: детали текста или плавность;
 *  - VP9 (в Chromium заметно чётче VP8 при том же битрейте для экрана), запасной VP8 включается SDK;
 *  - degradationPreference: sharp/balanced держат разрешение (читаемость), motion — частоту кадров;
 *  - дополнительный низкий simulcast-слой — зрителям со слабым каналом и миниатюрам не нужен полный поток.
 * Подписчики получают слой по размеру окна (adaptiveStream), в полноэкранном режиме — максимальный.
 *
 * «Экономный 720p» отличается: один слой без simulcast (серверу и сети нечего пересылать лишнего), VP8 (предсказуемый одиночный поток без
 * масштабируемого кодирования: SDK для VP9 принудительно выбирает режим «движение» и частоту до 5 к/с), потолок 800 кбит/с на самом отправителе.
 * Серверного перекодирования нет: SFU пересылает этот единственный поток.
 */
export function screenShareOptions(profile: ScreenProfile, withAudio: boolean, ecoKbps: number = ECO.kbps): {
  capture: ScreenShareCaptureOptions;
  publish: TrackPublishOptions;
} {
  const p = ScreenSharePresets;
  const common = { audio: withAudio, systemAudio: withAudio ? ("include" as const) : ("exclude" as const), surfaceSwitching: "include" as const,
                   selfBrowserSurface: "exclude" as const, suppressLocalAudioPlayback: true };
  switch (profile) {
    case "eco":
      return {
        capture: { ...common, resolution: { width: ECO.width, height: ECO.height, frameRate: ECO.fps }, contentHint: "text" },
        publish: { videoCodec: "vp8", simulcast: false, screenShareEncoding: { maxBitrate: clampEcoKbps(ecoKbps) * 1000, maxFramerate: ECO.fps },
                   screenShareSimulcastLayers: [], degradationPreference: "maintain-resolution" },
      };
    case "motion":
      return {
        capture: { ...common, resolution: p.h1080fps30.resolution, contentHint: "motion" },
        publish: { videoCodec: "vp9", screenShareEncoding: { maxBitrate: 6_000_000, maxFramerate: 30 },
                   screenShareSimulcastLayers: [p.h720fps15], degradationPreference: "maintain-framerate" },
      };
    case "balanced":
      return {
        capture: { ...common, resolution: p.h1080fps15.resolution, contentHint: "detail" },
        publish: { videoCodec: "vp9", screenShareEncoding: { maxBitrate: 3_500_000, maxFramerate: 20 },
                   screenShareSimulcastLayers: [p.h720fps15], degradationPreference: "balanced" },
      };
    default:
      return {
        capture: { ...common, resolution: p.h1080fps15.resolution, contentHint: "text" },
        publish: { videoCodec: "vp9", screenShareEncoding: { maxBitrate: 4_000_000, maxFramerate: 15 },
                   screenShareSimulcastLayers: [p.h720fps5], degradationPreference: "maintain-resolution" },
      };
  }
}

export const isScreenProfile = (v: string | null | undefined): v is ScreenProfile => v === "sharp" || v === "balanced" || v === "motion" || v === "eco";
export const PROFILE_ORDER: ScreenProfile[] = ["eco", "sharp", "balanced", "motion"];

// ---------------------------------------------------------------------------------------------- выбор профиля участником
const KEY = "pg:screen:profile";

/** Выбор участника хранится в браузере; нет выбора — профиль по умолчанию, заданный администратором. */
export function loadProfile(fallback: string | undefined, storage: Pick<Storage, "getItem"> | null = safeStorage()): ScreenProfile {
  let saved: string | null = null;
  try { saved = storage?.getItem(KEY) ?? null; } catch { /* хранилище недоступно — берём значение по умолчанию */ }
  if (isScreenProfile(saved)) return saved;
  return isScreenProfile(fallback) ? fallback : "sharp";
}
export function saveProfile(profile: ScreenProfile, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try { storage?.setItem(KEY, profile); } catch { /* не страшно: выбор не запомнится */ }
}
function safeStorage(): Storage | null { try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; } }

// ---------------------------------------------------------------------------------------------- применение к идущему показу
/** Во сколько раз нужно уменьшить картинку, чтобы вписать её в 1280×720 (1 — не нужно). Пропорции сохраняются. */
export function ecoScale(width: number | undefined, height: number | undefined): number {
  if (!width || !height) return 1;
  return Math.max(1, width / ECO.width, height / ECO.height);
}

export interface SenderStat { frameWidth?: number; frameHeight?: number; framesPerSecond?: number; targetBitrate?: number; qualityLimitationReason?: string }

export interface EcoReport {
  /** Уложился ли поток в параметры профиля (с допуском на кодек и сеть). */
  ok: boolean;
  text: string;
}

/** Честный итог по измерениям `getStats()`: что реально уходит, а не что запрошено. Допуск: разрешение ≤ 1280×720, частота ≤ 12, битрейт ≤ потолок + 15 %. */
export function ecoReport(s: SenderStat | undefined, kbps: number = ECO.kbps): EcoReport {
  if (!s || !s.frameWidth || !s.frameHeight) return { ok: false, text: "Экономный 720p: измерить исходящий поток пока не удалось." };
  const fps = Math.round(s.framesPerSecond ?? 0);
  const rate = s.targetBitrate ? Math.round(s.targetBitrate / 1000) : undefined;
  const sizeOk = s.frameWidth <= ECO.width && s.frameHeight <= ECO.height;
  const fpsOk = fps <= ECO.fps + 2;
  const rateOk = rate === undefined || rate <= kbps * 1.15;
  const facts = `${s.frameWidth}×${s.frameHeight}, ${fps} к/с${rate !== undefined ? `, цель ${rate} кбит/с` : ""}`;
  if (sizeOk && fpsOk && rateOk) return { ok: true, text: `Экономный 720p: уходит ${facts}.` };
  return { ok: false, text: `Экономный 720p: браузер не уложился точно в параметры профиля — фактически ${facts}. Применены ближайшие допустимые значения.` };
}

type TrackLike = Pick<LocalVideoTrack, "mediaStreamTrack" | "sender" | "getSenderStats">;

/**
 * Применить профиль к уже идущему показу экрана без остановки: ограничения захвата, подсказка содержимого, потолки на отправителе
 * (битрейт, частота, масштаб). Что нельзя поменять на лету (кодек, число слоёв), остаётся прежним до следующего показа — тогда "partial".
 */
export async function applyProfileLive(track: TrackLike | undefined, profile: ScreenProfile, ecoKbps: number = ECO.kbps): Promise<"live" | "partial" | "none"> {
  const sender = track?.sender, msTrack = track?.mediaStreamTrack;
  if (!track || !sender || !msTrack) return "none";
  const target = screenShareOptions(profile, false, ecoKbps);
  let full = true;
  try {
    if ("contentHint" in msTrack && target.capture.contentHint) (msTrack as MediaStreamTrack & { contentHint: string }).contentHint = target.capture.contentHint;
    const r = target.capture.resolution;
    if (r) await msTrack.applyConstraints({ width: { max: r.width }, height: { max: r.height }, frameRate: { max: r.frameRate ?? 15 } }).catch(() => { full = false; });
  } catch { full = false; }
  let layers = 1;
  try {
    const params = sender.getParameters();
    const enc = target.publish.screenShareEncoding;
    const settings = msTrack.getSettings?.() ?? {};
    const scale = profile === "eco" ? ecoScale(settings.width, settings.height) : 1;
    const list = params.encodings ?? [];
    layers = list.length || 1;
    list.forEach((e, i) => {
      if (enc && i === 0) { e.maxBitrate = enc.maxBitrate; e.maxFramerate = enc.maxFramerate; }
      if (i === 0) e.scaleResolutionDownBy = scale;
    });
    if (target.publish.degradationPreference) (params as RTCRtpSendParameters & { degradationPreference?: string }).degradationPreference = target.publish.degradationPreference;
    await sender.setParameters(params);
  } catch { return "none"; }
  return full && (profile !== "eco" || layers <= 1) ? "live" : "partial";
}

/** Измеренные параметры исходящего потока показа экрана (слой с наибольшим разрешением) — для сообщения пользователю. */
export async function measureScreen(track: TrackLike | undefined): Promise<SenderStat | undefined> {
  try {
    const stats = await track?.getSenderStats();
    if (!stats?.length) return undefined;
    return [...stats].sort((a, b) => (b.frameWidth ?? 0) - (a.frameWidth ?? 0))[0];
  } catch { return undefined; }
}
