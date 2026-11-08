/**
 * Автоматическое качество просмотра эфира: видео ↔ «только звук».
 * Главное правило: звук важнее картинки. Как только приём становится плохим, зритель переходит на дорожку только со звуком (она в десятки раз легче), а когда связь
 * восстановилась и это подтверждено замером, возвращается к видео. Решение принимается по всем доступным признакам сразу: остановки, запас буфера, оценка канала,
 * время загрузки сегментов, пропуск кадров устройством, фон страницы, режим экономии трафика и медленная сеть.
 *
 * Модуль чистый (без DOM и таймеров): время приходит параметром, поэтому поведение проверяется тестами по последовательностям замеров.
 */
export type Mode = "video" | "audio";

export interface Sample {
  buffer: number;                  // секунд запаса впереди воспроизведения
  stalls: number;                  // остановок плеера всего (накопительно)
  stallMs: number;                 // миллисекунд простоя всего (накопительно)
  bwKbps: number | null;           // оценка канала hls.js; null — оценки нет (например, на дорожке только со звуком)
  probeKbps: number | null;        // замер скорости пробной загрузкой видеосегмента; null — не измерялся
  fragRatio: number | null;        // время загрузки сегмента ÷ его длительность; > 1 — канал не успевает
  dropRatio: number | null;        // доля кадров, пропущенных устройством за последние секунды
  hidden: boolean;                 // страница в фоне
  saveData: boolean;               // включён режим экономии трафика в браузере
  slowNet: boolean;                // браузер сообщает о медленной сети (2g)
  needKbps: number;                // сколько нужно видео, чтобы идти без остановок
}

export interface State {
  mode: Mode;
  since: number;                   // когда режим включён (мс)
  holdUntil: number;               // раньше этого момента назад к видео не возвращаемся
  failedUpgrades: number;          // подряд неудачных возвратов к видео
  lastUpgradeAt: number;           // когда вернулись к видео
  stallLog: { at: number; ms: number }[];
  low: Record<"buffer" | "bw" | "frag" | "drop" | "calm" | "hidden", number | null>;   // с какого момента признак держится (null — нет)
  reason: string;
  prevStalls: number;
  prevStallMs: number;
}

export const MIN_HOLD_MS = 25_000;
export const MAX_HOLD_MS = 300_000;
export const WINDOW_MS = 30_000;

export const initial = (now: number): State => ({
  mode: "video", since: now, holdUntil: 0, failedUpgrades: 0, lastUpgradeAt: 0, stallLog: [], reason: "",
  low: { buffer: null, bw: null, frag: null, drop: null, calm: null, hidden: null }, prevStalls: 0, prevStallMs: 0,
});

const hold = (failed: number) => Math.min(MAX_HOLD_MS, MIN_HOLD_MS * 2 ** failed);

/** Держится ли признак уже `ms` миллисекунд подряд. */
function keep(low: State["low"], key: keyof State["low"], on: boolean, now: number, ms: number): boolean {
  if (!on) { low[key] = null; return false; }
  if (low[key] === null) low[key] = now;
  return now - (low[key] as number) >= ms;
}

export interface Decision { mode: Mode; changed: boolean; reason: string }

/** Один шаг: свежий замер → режим. Состояние меняется на месте, возвращается решение. */
export function step(st: State, s: Sample, now: number): Decision {
  // остановки за последние 30 с
  const dStalls = Math.max(0, s.stalls - st.prevStalls), dMs = Math.max(0, s.stallMs - st.prevStallMs);
  st.prevStalls = s.stalls; st.prevStallMs = s.stallMs;
  for (let i = 0; i < dStalls; i++) st.stallLog.push({ at: now, ms: i === 0 ? dMs : 0 });
  st.stallLog = st.stallLog.filter((x) => now - x.at <= WINDOW_MS);
  const stallsNow = st.stallLog.length, stallMsNow = st.stallLog.reduce((a, x) => a + x.ms, 0);

  if (st.mode === "video") {
    let why = "";
    if (s.saveData) why = "включён режим экономии трафика";
    else if (s.slowNet) why = "медленная сеть";
    else if (keep(st.low, "hidden", s.hidden, now, 10_000)) why = "страница в фоне";
    else if (stallsNow >= 2 || stallMsNow >= 4000) why = "частые остановки";
    else if (keep(st.low, "buffer", s.buffer < 0.5, now, 4000)) why = "пустой буфер";
    else if (keep(st.low, "bw", s.bwKbps !== null && s.bwKbps < s.needKbps * 1.2, now, 8000)) why = "канал не тянет видео";
    else if (keep(st.low, "frag", s.fragRatio !== null && s.fragRatio > 0.9, now, 6000)) why = "сегменты грузятся слишком долго";
    else if (keep(st.low, "drop", s.dropRatio !== null && s.dropRatio > 0.25, now, 10_000)) why = "устройство не успевает показывать видео";
    if (!why) {
      if (st.lastUpgradeAt && now - st.lastUpgradeAt > 60_000 && st.failedUpgrades) st.failedUpgrades = 0;    // минуту держалось: прошлые неудачи прощены
      return { mode: "video", changed: false, reason: "" };
    }
    if (st.lastUpgradeAt && now - st.lastUpgradeAt < 20_000) st.failedUpgrades += 1;                          // вернулись и сразу сорвались: следующая попытка позже
    st.mode = "audio"; st.since = now; st.reason = why;
    st.holdUntil = now + hold(st.failedUpgrades);
    st.low = { buffer: null, bw: null, frag: null, drop: null, calm: null, hidden: null };
    st.stallLog = [];
    return { mode: "audio", changed: true, reason: why };
  }

  // режим «только звук»: возвращаемся к видео, когда всё спокойно и это подтверждено замером
  const blocked = s.hidden || s.saveData || s.slowNet || now < st.holdUntil;
  const speed = s.probeKbps ?? null;
  const fast = speed !== null && speed >= s.needKbps * 2;
  if (!keep(st.low, "calm", !blocked && fast && s.buffer >= 1, now, 10_000)) return { mode: "audio", changed: false, reason: st.reason };
  st.mode = "video"; st.since = now; st.lastUpgradeAt = now; st.reason = "";
  st.low = { buffer: null, bw: null, frag: null, drop: null, calm: null, hidden: null };
  st.stallLog = [];
  return { mode: "video", changed: true, reason: "связь восстановилась" };
}

export const REASON_TEXT: Record<string, string> = {
  "включён режим экономии трафика": "Только звук: включён режим экономии трафика",
};

export const audioNote = (reason: string) => `Только звук: ${reason}. Видео вернётся само, когда связь наладится.`;
