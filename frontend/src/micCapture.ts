/**
 * Захват микрофона: точная причина отказа и защита от собственных гонок.
 *
 * Причина. Chromium (с версии 152 — и Edge, и приложение для Windows на Electron) различает отказ WASAPI текстом ошибки getUserMedia
 * (third_party/blink/renderer/modules/mediastream/user_media_processor.cc):
 *   NotReadableError «Device in use»                  — AUDCLNT_E_DEVICE_IN_USE: устройство занято (монопольный захват другой программой
 *                                                        или ещё не освобождено нашим же предыдущим потоком);
 *   NotAllowedError  «Permission denied by system»    — E_ACCESSDENIED: конфиденциальность Windows или защитная программа;
 *   NotReadableError «Device was removed»             — устройство отключено или стало недействительным;
 *   NotReadableError «Could not start audio source»   — любой другой отказ: драйвер, формат, вмешательство защитной программы
 *                                                        (так блокирует Kaspersky Endpoint Security, electron/electron#43524).
 * Раньше любая NotReadableError показывалась как «занят другой программой» — и вела по ложному следу монопольного режима.
 *
 * Гонки. Сразу после остановки потока Windows может ещё держать устройство: повторное открытие получает «Device in use», хотя никакой
 * чужой программы нет (так Chromium объяснял свой эксперимент WasapiInputDeviceInUseRetry, 2026-07; эксперимент снят 2026-08, повтора
 * в движке нет). Наши сценарии «закрыть и сразу открыть» — проверка оборудования → вход, смена режима шумоподавления, смена микрофона,
 * восстановление после пропажи устройства. Поэтому запросы микрофона выполняются по одному, а временный отказ повторяется
 * ограниченно (суммарно ≈ 1,3 с). Настоящую монопольную блокировку повтор не обходит: после последней попытки ошибка возвращается как есть.
 */

export type MicErrorKind = "in_use" | "start_failed" | "system_denied" | "user_denied" | "removed" | "not_found" | "overconstrained" | "aborted" | "insecure" | "other";

const text = (e: unknown) => String((e as { message?: unknown })?.message ?? "");
const nameOf = (e: unknown) => String((e as { name?: unknown })?.name ?? "");

export function micErrorKind(e: unknown): MicErrorKind {
  const n = nameOf(e), m = text(e).toLowerCase();
  if (n === "NotReadableError" || n === "TrackStartError") {
    if (/device in use|already in use|in use by another/.test(m)) return "in_use";
    if (/device was removed|was removed/.test(m)) return "removed";
    return "start_failed";
  }
  if (n === "NotAllowedError" || n === "PermissionDeniedError") return /by system|system permissions/.test(m) ? "system_denied" : "user_denied";
  if (n === "NotFoundError" || n === "DevicesNotFoundError") return "not_found";
  if (n === "OverconstrainedError" || n === "ConstraintNotSatisfiedError") return "overconstrained";
  if (n === "AbortError") return "aborted";
  if (n === "SecurityError") return "insecure";
  return "other";
}

/** Отказы, после которых повторить через долю секунды имеет смысл: устройство освобождается или драйвер ещё не готов. */
export const TRANSIENT: ReadonlySet<MicErrorKind> = new Set(["in_use", "start_failed", "aborted"]);
export const RETRY_DELAYS_MS = [120, 350, 800];

export interface CaptureReport { kind: MicErrorKind; attempts: number; recovered: boolean; message: string }

const wantsAudio = (c?: MediaStreamConstraints) => !!c && c.audio !== undefined && c.audio !== false;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Обёртка над getUserMedia: запросы со звуком выполняются по очереди, временный отказ повторяется по RETRY_DELAYS_MS.
 * `onReport` получает итог, если был хотя бы один отказ (успех после повтора — подтверждение гонки освобождения, а не чужой блокировки).
 */
export function guardGetUserMedia(original: (c?: MediaStreamConstraints) => Promise<MediaStream>, onReport: (r: CaptureReport) => void,
  delays: number[] = RETRY_DELAYS_MS): (c?: MediaStreamConstraints) => Promise<MediaStream> {
  let chain: Promise<unknown> = Promise.resolve();
  return (c?: MediaStreamConstraints) => {
    if (!wantsAudio(c)) return original(c);
    const run = async () => {
      let first: unknown = null;
      for (let i = 0; ; i++) {
        try {
          const s = await original(c);
          if (first) onReport({ kind: micErrorKind(first), attempts: i + 1, recovered: true, message: text(first).slice(0, 120) });
          return s;
        } catch (e) {
          first = first ?? e;
          const kind = micErrorKind(e);
          if (!TRANSIENT.has(kind) || i >= delays.length) {
            onReport({ kind, attempts: i + 1, recovered: false, message: text(e).slice(0, 120) });
            throw e;
          }
          await sleep(delays[i]);
        }
      }
    };
    const p = chain.then(run, run);
    chain = p.catch(() => undefined);
    return p;
  };
}

/** Подключается один раз при старте приложения: покрывает и собственные вызовы, и вызовы LiveKit (он обращается к navigator.mediaDevices). */
export function installMicCaptureGuard(onReport: (r: CaptureReport) => void): void {
  const md = typeof navigator !== "undefined" ? navigator.mediaDevices : undefined;
  if (!md?.getUserMedia || (md as { __pgGuard?: boolean }).__pgGuard) return;
  const original = md.getUserMedia.bind(md);
  md.getUserMedia = guardGetUserMedia(original, onReport);
  (md as { __pgGuard?: boolean }).__pgGuard = true;
}
