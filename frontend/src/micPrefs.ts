import type { AudioCaptureOptions } from "livekit-client";

/**
 * Настройки микрофона пользователя (хранятся в браузере, на сервер не отправляются).
 *  - noiseSuppression — шумоподавление браузера (вентиляторы, клавиатура, фон); для студийного микрофона можно выключить;
 *  - echoCancellation / autoGainControl — эхоподавление и автоусиление, по умолчанию включены (речь на ноутбуках и гарнитурах);
 *  - releaseOnMute — «не держать микрофон»: при выключении микрофона устройство освобождается совсем, и им могут пользоваться другие программы
 *    (по умолчанию выключено: так включение микрофона мгновенное, а Bluetooth-гарнитура не переключает профиль).
 */
export interface MicPrefs { noiseSuppression: boolean; echoCancellation: boolean; autoGainControl: boolean; releaseOnMute: boolean }

export const DEFAULT_MIC_PREFS: MicPrefs = { noiseSuppression: true, echoCancellation: true, autoGainControl: true, releaseOnMute: false };
const KEY = "room.micPrefs.v1";

type Store = Pick<Storage, "getItem" | "setItem">;
const defaultStore = (): Store | null => { try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; } };

export function loadMicPrefs(store: Store | null = defaultStore()): MicPrefs {
  try {
    const raw = store?.getItem(KEY);
    if (!raw) return { ...DEFAULT_MIC_PREFS };
    const o = JSON.parse(raw) as Partial<MicPrefs>;
    const pick = (k: keyof MicPrefs) => (typeof o[k] === "boolean" ? (o[k] as boolean) : DEFAULT_MIC_PREFS[k]);
    return { noiseSuppression: pick("noiseSuppression"), echoCancellation: pick("echoCancellation"), autoGainControl: pick("autoGainControl"), releaseOnMute: pick("releaseOnMute") };
  } catch { return { ...DEFAULT_MIC_PREFS }; }
}

export function saveMicPrefs(p: MicPrefs, store: Store | null = defaultStore()): void {
  try { store?.setItem(KEY, JSON.stringify(p)); } catch { /* хранилище недоступно — настройка действует до перезагрузки страницы */ }
}

/** Параметры захвата для getUserMedia/LiveKit. */
export function captureOptions(p: MicPrefs = loadMicPrefs()): AudioCaptureOptions {
  return { noiseSuppression: p.noiseSuppression, echoCancellation: p.echoCancellation, autoGainControl: p.autoGainControl };
}
