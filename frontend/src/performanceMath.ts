/** Показатели страницы «Производительность»: форматирование и пояснения. Чистые функции — проверяются тестами. */

export const NO_DATA = "Нет данных";

export const num = (v: number | null | undefined, unit = "", digits = 0): string =>
  v == null || !Number.isFinite(v) ? NO_DATA : `${v.toLocaleString("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: 0 })}${unit ? ` ${unit}` : ""}`;

export const pct = (v: number | null | undefined): string => num(v, "%", 0);

export function mbps(kbps: number | null | undefined): string {
  if (kbps == null || !Number.isFinite(kbps)) return NO_DATA;
  return kbps >= 1000 ? `${(kbps / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} Мбит/с` : `${Math.round(kbps)} кбит/с`;
}

export function gib(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return NO_DATA;
  return `${(bytes / 1073741824).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ГБ`;
}

export type Level = "ok" | "busy" | "overloaded";
export const LEVEL_TEXT: Record<Level, { text: string; tone: "ok" | "warn" | "bad"; hint: string }> = {
  ok: { text: "Нагрузка в норме", tone: "ok", hint: "Звонки и распознавание речи получают достаточно процессора; фоновые задачи идут без задержек." },
  busy: { text: "Сервер занят", tone: "warn", hint: "Во время встреч тяжёлые фоновые задачи (сведение записи, волновая форма, карта разговора) ждут, пока нагрузка снизится." },
  overloaded: { text: "Сервер перегружен", tone: "bad", hint: "Во время встреч откладываются и протоколы. Звонок и распознавание речи не задерживаются специально, но сами могут запаздывать." },
};

/** Цвет полосы заполнения: до 70 % — обычный, до 90 % — предупреждение, выше — красный. */
export const barTone = (v: number | null | undefined): "" | "warn" | "bad" => (v == null ? "" : v >= 90 ? "bad" : v >= 70 ? "warn" : "");

/** Подпись очереди распознавания: пусто и мало — норма, много — стенограмма запаздывает. */
export function asrQueueText(q: number | null | undefined): string {
  if (q == null) return NO_DATA;
  return q === 0 ? "очередь пуста" : q < 8 ? `${q} в очереди` : `${q} в очереди — стенограмма запаздывает`;
}

/** Поток (aудио/видео) одной строкой: «12 аудио, 3 видео». */
export function tracksText(audio: number | null | undefined, video: number | null | undefined): string {
  return audio == null && video == null ? NO_DATA : `${audio ?? 0} аудио, ${video ?? 0} видео`;
}
