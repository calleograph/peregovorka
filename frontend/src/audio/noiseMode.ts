import type { LocalAudioTrack } from "livekit-client";

/** Режимы шумоподавления: выкл — звук как есть; стандартный — шумоподавление браузера; усиленный — RNNoise на устройстве (браузерное при этом выключается, чтобы не обрабатывать дважды). */
export type NoiseMode = "off" | "standard" | "enhanced";
export const NOISE_MODES: NoiseMode[] = ["off", "standard", "enhanced"];
export const NOISE_LABEL: Record<NoiseMode, string> = { off: "Выключено", standard: "Стандартное", enhanced: "Усиленное" };
export const NOISE_HINT: Record<NoiseMode, string> = {
  off: "Звук без обработки — для студийного микрофона и тихой комнаты.",
  standard: "Шумоподавление браузера: вентиляторы, фон. Подходит большинству.",
  enhanced: "Нейросеть RNNoise на вашем устройстве: сильнее убирает клавиатуру и разговоры вдали, но чуть нагружает процессор. Если не запустится — включится стандартное.",
};

export const isNoiseMode = (v: unknown): v is NoiseMode => v === "off" || v === "standard" || v === "enhanced";

/** Включать ли шумоподавление браузера при захвате: в «усиленном» режиме — нет (иначе двойная обработка искажает речь). */
export const browserNoiseSuppression = (m: NoiseMode): boolean => m === "standard";

/** Старые настройки хранили только флаг «шумоподавление включено». */
export const migrateNoiseMode = (stored: { noiseMode?: unknown; noiseSuppression?: unknown }): NoiseMode =>
  isNoiseMode(stored.noiseMode) ? stored.noiseMode : stored.noiseSuppression === false ? "off" : "standard";

export interface NoiseResult { mode: NoiseMode; fellBack: boolean; reason?: string }

/**
 * Применяет режим к опубликованному треку. Усиленный режим подключает RNNoise; при любой ошибке запуска (или падении обработчика позже)
 * возвращается стандартный режим — разговор не должен прерываться из-за шумоподавления. `restart` перезапускает захват с нужными ограничениями браузера.
 */
export async function applyNoiseMode(
  track: LocalAudioTrack, mode: NoiseMode,
  restart: (browserNs: boolean) => Promise<void>, onLateFailure: (reason: string) => void,
): Promise<NoiseResult> {
  const hadProcessor = !!track.getProcessor();
  if (hadProcessor) await track.stopProcessor().catch(() => undefined);
  if (mode !== "enhanced") {
    await restart(browserNoiseSuppression(mode));
    return { mode, fellBack: false };
  }
  try {
    const { RnnoiseProcessor, rnnoiseSupported } = await import("./rnnoise");
    if (!rnnoiseSupported()) throw new Error("not_supported");
    await restart(false);
    const p = new RnnoiseProcessor();
    p.onError = (r) => onLateFailure(r);
    await withTimeout(track.setProcessor(p), 8000);
    return { mode, fellBack: false };
  } catch (e) {
    await track.stopProcessor().catch(() => undefined);
    await restart(true).catch(() => undefined);
    return { mode: "standard", fellBack: true, reason: e instanceof Error ? e.message : String(e) };
  }
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    p.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}
