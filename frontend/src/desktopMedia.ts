// Устройства и обработка звука, выбранные в настройках приложения Windows («Настройки → Звук и видео»). В браузере ничего не делает.
// Выбор приходит по НАЗВАНИЮ устройства: идентификаторы Chromium у каждого адреса сайта свои, а название одно. Это только значения по умолчанию: во встрече пользователь выбирает сам.
import { desktop } from "./desktop";
import { isNoiseMode, type NoiseMode } from "./audio/noiseMode";

export interface DesktopMedia { mic: string; speaker: string; camera: string; noise: NoiseMode; echo: boolean; agc: boolean }

let cache: DesktopMedia | null = null;

export function parseDesktopMedia(v: unknown): DesktopMedia | null {
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  const s = (x: unknown) => (typeof x === "string" ? x.slice(0, 200) : "");
  return { mic: s(o.mic), speaker: s(o.speaker), camera: s(o.camera), noise: isNoiseMode(o.noise) ? o.noise : "enhanced", echo: o.echo !== false, agc: o.agc !== false };
}

/** Устройство с таким названием; пустое название или нет совпадения — null (системное по умолчанию). */
export function pickByLabel<T extends { label: string }>(list: T[], label: string): T | null {
  return label ? list.find((d) => d.label === label) ?? null : null;
}

/** Значения по умолчанию из настроек приложения (null — не в приложении или приложение старой версии). Результат кэшируется для синхронных вызовов. */
export async function loadDesktopMedia(): Promise<DesktopMedia | null> {
  const bridge = desktop?.media;
  if (!bridge) return null;
  try { cache = parseDesktopMedia(await bridge.get()); } catch { cache = null; }
  return cache;
}
export const desktopMediaCached = (): DesktopMedia | null => cache;

/** Открыть системные настройки звука Windows (только в приложении). false — приложение не умеет или отказало. */
export async function openWindowsSound(kind: "mic-classic" | "sound" | "privacy-mic" = "mic-classic"): Promise<boolean> {
  if (!desktop?.openSoundSettings) return false;
  try { return ((await desktop.openSoundSettings(kind)) as { ok?: boolean } | null)?.ok === true; } catch { return false; }
}

if (desktop?.media) void loadDesktopMedia();
