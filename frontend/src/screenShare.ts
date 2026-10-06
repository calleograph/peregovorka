import { ScreenSharePresets, type ScreenShareCaptureOptions, type TrackPublishOptions } from "livekit-client";

export type ScreenProfile = "sharp" | "balanced" | "motion";

export const PROFILE_LABELS: Record<ScreenProfile, string> = {
  sharp: "Чёткость (текст, слайды, код)",
  balanced: "Сбалансированный",
  motion: "Плавность (видео, анимация)",
};

/**
 * Параметры «быстрой и качественной» трансляции экрана одним кликом.
 *  - захват до 1080p; contentHint подсказывает кодеку, что важнее: детали текста или плавность;
 *  - VP9 (в Chromium заметно чётче VP8 при том же битрейте для экрана), запасной VP8 включается SDK;
 *  - degradationPreference: sharp/balanced держат разрешение (читаемость), motion — частоту кадров;
 *  - дополнительный низкий simulcast-слой — зрителям со слабым каналом и миниатюрам не нужен полный поток.
 * Подписчики получают слой по размеру окна (adaptiveStream), в полноэкранном режиме — максимальный.
 */
export function screenShareOptions(profile: ScreenProfile, withAudio: boolean): {
  capture: ScreenShareCaptureOptions;
  publish: TrackPublishOptions;
} {
  const p = ScreenSharePresets;
  const common = { audio: withAudio, systemAudio: withAudio ? ("include" as const) : ("exclude" as const), surfaceSwitching: "include" as const,
                   selfBrowserSurface: "exclude" as const, suppressLocalAudioPlayback: true };
  switch (profile) {
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

export const isScreenProfile = (v: string): v is ScreenProfile => v === "sharp" || v === "balanced" || v === "motion";
