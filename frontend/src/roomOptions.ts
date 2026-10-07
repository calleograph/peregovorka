import { ScreenSharePresets, VideoPresets, type AudioCaptureOptions, type RoomOptions } from "livekit-client";
import { captureOptions, loadMicPrefs, type MicPrefs } from "./micPrefs";

/** Захват микрофона: подавление эха/шума и автоусиление — для речи; то же самое используется при предварительном getUserMedia. */
export const MIC_CAPTURE: AudioCaptureOptions = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

/**
 * Настройки Room: камера и показ экрана настраиваются РАЗНО.
 *  - камера: 720p/24 к/с, VP8 (дёшево по CPU у отправителя и поддерживается всеми), simulcast 360p/180p — зрителям в плитках хватает малого слоя;
 *  - показ экрана: приоритет читаемости текста — стабильное разрешение и умеренный FPS (15, а не 30); профиль выбирается при публикации
 *    (screenShare.ts: sharp/balanced/motion). Для сравнения: Jitsi по умолчанию берёт для экрана 5 к/с («выше FPS — хуже разрешение»).
 *  - adaptiveStream/dynacast: получатели запрашивают слой по размеру окна, отправитель не кодирует слои, которых никто не смотрит.
 */
export function buildRoomOptions(prefs: MicPrefs = loadMicPrefs()): RoomOptions {
  return {
    adaptiveStream: true,
    dynacast: true,
    audioCaptureDefaults: captureOptions(prefs),
    videoCaptureDefaults: { resolution: VideoPresets.h720.resolution },
    publishDefaults: {
      videoCodec: "vp8",
      simulcast: true,
      videoEncoding: { maxBitrate: 1_500_000, maxFramerate: 24 },
      videoSimulcastLayers: [VideoPresets.h360, VideoPresets.h180],
      screenShareEncoding: { maxBitrate: 3_000_000, maxFramerate: 15 },
      screenShareSimulcastLayers: [ScreenSharePresets.h720fps5],
      dtx: true,
      stopMicTrackOnMute: prefs.releaseOnMute,  // «не держать микрофон»: выключили — устройство свободно для других программ
    },
  };
}
