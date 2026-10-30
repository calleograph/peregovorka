/**
 * Загрузка вложений с подтверждением.
 *
 * Раньше файл уходил на сервер прямо из `File`, полученного от буфера обмена или перетаскивания: такой `File` может лениво читаться из источника (буфер обмена,
 * временный файл программы-скриншотера) уже во время отправки, а отправитель видел локальный предпросмотр и считал файл отправленным, даже если на сервер доехала
 * пустота или обрубок. Теперь:
 *   1. байты читаются в память сразу и проверяются (не пусто);
 *   2. считается SHA-256, он уходит в заголовке `X-Content-SHA256`: сервер отказывает, если тело дошло иным (прокси и антивирусы иногда обрезают или подменяют тело);
 *   3. сервер в ответе называет размер и SHA-256 принятых байт; вложение считается отправленным, только если они совпали с нашими.
 * Сетевые сбои и 502/503/504 повторяются (вложение ещё не создано, повтор безопасен: недоотправленные вложения сервер убирает сам).
 */
import { ApiError } from "./api";

export interface Upload { bytes: ArrayBuffer; sha256: string | null }

export const RETRY_DELAYS_MS = [700, 2000];

export async function readUpload(file: Blob): Promise<Upload> {
  let bytes: ArrayBuffer;
  try { bytes = await file.arrayBuffer(); }
  catch { throw new ApiError(0, "unreadable", "Не удалось прочитать файл. Если это скриншот или файл из другой программы, скопируйте или выберите его заново."); }
  if (!bytes.byteLength) throw new ApiError(0, "empty", "Файл пустой: из буфера обмена или с диска не пришло ни одного байта. Скопируйте изображение или выберите файл заново.");
  return { bytes, sha256: await sha256Hex(bytes) };
}

export async function sha256Hex(bytes: ArrayBuffer): Promise<string | null> {
  const subtle = typeof crypto !== "undefined" ? crypto.subtle : undefined;      // только в защищённом контексте (https, localhost)
  if (!subtle) return null;
  try { return [...new Uint8Array(await subtle.digest("SHA-256", bytes))].map((b) => b.toString(16).padStart(2, "0")).join(""); }
  catch { return null; }
}

/** Сервер ответил «создано» — убедиться, что принято именно то, что отправлено. */
export function confirmUpload(res: { size?: number; sha256?: string }, up: Upload): void {
  if (typeof res.size === "number" && res.size !== up.bytes.byteLength) {
    throw new ApiError(0, "size_mismatch", `Сервер принял ${res.size} байт из ${up.bytes.byteLength}: файл дошёл не целиком. Повторите отправку.`);
  }
  if (up.sha256 && res.sha256 && res.sha256.toLowerCase() !== up.sha256) {
    throw new ApiError(0, "hash_mismatch", "Файл дошёл до сервера изменённым (контрольные суммы не совпали). Повторите отправку.");
  }
}

const transient = (e: unknown) => e instanceof ApiError && (e.status === 0 && e.code === "network" || [502, 503, 504].includes(e.status));

export async function withRetry<T>(run: () => Promise<T>, delays: number[] = RETRY_DELAYS_MS, sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms))): Promise<T> {
  for (let i = 0; ; i++) {
    try { return await run(); }
    catch (e) {
      if (i >= delays.length || !transient(e)) throw e;
      await sleep(delays[i]);
    }
  }
}
