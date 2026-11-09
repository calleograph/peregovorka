/**
 * Автоматическое качество просмотра эфира: видео → низкое качество (360p) → «только звук».
 * Главное правило: звук важнее картинки. Как только приём становится плохим, зритель переходит на ступень ниже (сначала облегчённое видео, если сервер его готовит,
 * потом дорожку только со звуком: она в десятки раз легче), а когда связь восстановилась и это подтверждено замером, поднимается обратно. Решение принимается по всем
 * доступным признакам сразу: остановки, запас буфера, оценка канала, время загрузки сегментов, пропуск кадров устройством, фон страницы, режим экономии трафика и медленная сеть.
 *
 * После каждого переключения 10 секунд не считаем остановки и пустой буфер: новый поток только загружается, это не плохая связь. Если зритель выбрал качество сам
 * (`manual`), режим не меняется: автоматика только подсказывает (`advice`), что картинка подвисает.
 *
 * Модуль чистый (без DOM и таймеров): время приходит параметром, поведение проверяется тестами по последовательностям замеров.
 */
export type Mode = "video" | "low" | "audio";

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
  lowNeedKbps?: number | null;     // сколько нужно низкому качеству; нет значения — сервер его не предлагает (не готово)
}

export interface State {
  mode: Mode;
  manual: boolean;                 // качество выбрал зритель: не переключаем, только подсказываем
  since: number;                   // когда режим включён (мс)
  graceUntil: number;              // до этого момента остановки и буфер не считаются: новый поток загружается
  holdUntil: number;               // раньше этого момента вверх не поднимаемся
  failedUpgrades: number;          // подряд неудачных подъёмов
  lastUpgradeAt: number;           // когда поднялись
  stallLog: { at: number; ms: number }[];
  low: Record<"buffer" | "bw" | "frag" | "drop" | "calm" | "hidden", number | null>;   // с какого момента признак держится (null — нет)
  reason: string;
  prevStalls: number;
  prevStallMs: number;
}

export const MIN_HOLD_MS = 25_000;
export const MAX_HOLD_MS = 300_000;
export const WINDOW_MS = 30_000;
export const GRACE_MS = 10_000;

const blankLow = (): State["low"] => ({ buffer: null, bw: null, frag: null, drop: null, calm: null, hidden: null });

/** Начальное состояние. `seed` — текущие накопительные счётчики плеера: без них прежние остановки сочтутся новыми и режим тут же откатится (так было при ручном «Включить видео»). */
export const initial = (now: number, seed: { stalls?: number; stallMs?: number; mode?: Mode; manual?: boolean; grace?: boolean } = {}): State => ({
  mode: seed.mode ?? "video", manual: !!seed.manual, since: now, graceUntil: seed.grace ? now + GRACE_MS : 0, holdUntil: 0, failedUpgrades: 0, lastUpgradeAt: 0, stallLog: [], reason: "",
  low: blankLow(), prevStalls: seed.stalls ?? 0, prevStallMs: seed.stallMs ?? 0,
});

const hold = (failed: number) => Math.min(MAX_HOLD_MS, MIN_HOLD_MS * 2 ** failed);

/** Держится ли признак уже `ms` миллисекунд подряд. */
function keep(low: State["low"], key: keyof State["low"], on: boolean, now: number, ms: number): boolean {
  if (!on) { low[key] = null; return false; }
  if (low[key] === null) low[key] = now;
  return now - (low[key] as number) >= ms;
}

export interface Decision { mode: Mode; changed: boolean; reason: string; up?: boolean; advice?: string }

/** Что не так с приёмом сейчас (пусто — всё в порядке). Остановки и буфер в первые секунды после переключения не считаются. */
function trouble(st: State, s: Sample, now: number, stallsNow: number, stallMsNow: number, need: number): { why: string; env: boolean } {
  const settling = now < st.graceUntil;
  if (s.saveData) return { why: "включён режим экономии трафика", env: true };
  if (s.slowNet) return { why: "медленная сеть", env: true };
  if (keep(st.low, "hidden", s.hidden, now, 10_000)) return { why: "страница в фоне", env: true };
  if (settling) { st.low.buffer = st.low.bw = st.low.frag = st.low.drop = null; return { why: "", env: false }; }
  if (stallsNow >= 2 || stallMsNow >= 4000) return { why: "частые остановки", env: false };
  if (keep(st.low, "buffer", s.buffer < 0.5, now, 4000)) return { why: "пустой буфер", env: false };
  if (keep(st.low, "bw", s.bwKbps !== null && s.bwKbps < need * 1.2, now, 8000)) return { why: st.mode === "video" ? "канал не тянет видео" : "канал не тянет и низкое качество", env: false };
  if (keep(st.low, "frag", s.fragRatio !== null && s.fragRatio > 0.9, now, 6000)) return { why: "сегменты грузятся слишком долго", env: false };
  if (keep(st.low, "drop", s.dropRatio !== null && s.dropRatio > 0.25, now, 10_000)) return { why: "устройство не успевает показывать видео", env: false };
  return { why: "", env: false };
}

/** Один шаг: свежий замер → режим. Состояние меняется на месте, возвращается решение. */
export function step(st: State, s: Sample, now: number): Decision {
  // остановки за последние 30 с; пока новый поток загружается, их не записываем
  const dStalls = Math.max(0, s.stalls - st.prevStalls), dMs = Math.max(0, s.stallMs - st.prevStallMs);
  st.prevStalls = s.stalls; st.prevStallMs = s.stallMs;
  if (now >= st.graceUntil) for (let i = 0; i < dStalls; i++) st.stallLog.push({ at: now, ms: i === 0 ? dMs : 0 });
  st.stallLog = st.stallLog.filter((x) => now - x.at <= WINDOW_MS);
  const stallsNow = st.stallLog.length, stallMsNow = st.stallLog.reduce((a, x) => a + x.ms, 0);
  const lowNeed = s.lowNeedKbps && s.lowNeedKbps > 0 ? s.lowNeedKbps : 0;

  if (st.mode !== "audio") {
    const { why, env } = trouble(st, s, now, stallsNow, stallMsNow, st.mode === "video" ? s.needKbps : lowNeed || s.needKbps / 4);
    if (!why) {
      if (st.lastUpgradeAt && now - st.lastUpgradeAt > 60_000 && st.failedUpgrades) st.failedUpgrades = 0;    // минуту держалось: прошлые неудачи прощены
      return st.mode === "low" && !st.manual ? climb(st, s, now, lowNeed) : { mode: st.mode, changed: false, reason: "" };
    }
    if (st.manual) return { mode: st.mode, changed: false, reason: "", advice: why };                          // выбрал сам: не переключаем, только подсказываем
    const to: Mode = st.mode === "video" && lowNeed && !env ? "low" : "audio";                                // ступень вниз; среда (фон, экономия, 2g) — сразу звук
    if (st.lastUpgradeAt && now - st.lastUpgradeAt < 20_000) st.failedUpgrades += 1;                          // поднялись и сразу сорвались: следующая попытка позже
    st.mode = to; st.since = now; st.reason = why;
    st.graceUntil = to === "audio" ? 0 : now + GRACE_MS;
    st.holdUntil = now + hold(st.failedUpgrades);
    st.low = blankLow(); st.stallLog = [];
    return { mode: to, changed: true, reason: why, up: false };
  }

  // «только звук»: поднимаемся, когда всё спокойно и это подтверждено замером
  if (st.manual) return { mode: "audio", changed: false, reason: "" };
  return climb(st, s, now, lowNeed);
}

/** Подъём из «только звука» или низкого качества. Низкое качество поднимается к видео, звук — к низкому качеству или сразу к видео, если канал позволяет. */
function climb(st: State, s: Sample, now: number, lowNeed: number): Decision {
  const blocked = s.hidden || s.saveData || s.slowNet || now < st.holdUntil || now < st.graceUntil;      // новый поток сначала должен установиться
  const speed = s.probeKbps ?? null;
  const toVideo = speed !== null && speed >= s.needKbps * 2;
  const toLow = st.mode === "audio" && lowNeed > 0 && speed !== null && speed >= lowNeed * 1.4;       // низкому качеству запаса нужно меньше, чем видео: оно нарочно лёгкое
  const to: Mode | null = toVideo ? "video" : toLow ? "low" : null;
  if (!keep(st.low, "calm", !blocked && to !== null && s.buffer >= 1, now, 10_000)) return { mode: st.mode, changed: false, reason: st.mode === "audio" ? st.reason : "" };
  st.mode = to as Mode; st.since = now; st.lastUpgradeAt = now; st.reason = ""; st.graceUntil = now + GRACE_MS;
  st.low = blankLow(); st.stallLog = [];
  return { mode: st.mode, changed: true, reason: "связь восстановилась", up: true };
}

/** Тексты для зрителя: спокойно, без тревожных слов; что случилось, что делать и что вернётся само. */
export const audioNote = (reason: string) => `Связь с эфиром слабая (${reason}), поэтому включён режим «Только звук»: вы ничего не пропустите. Видео вернётся само, как только связь наладится.`;
export const lowNote = (reason: string) => `Связь с эфиром слабая (${reason}), поэтому включено низкое качество: эфир идёт без остановок. Хорошее качество вернётся само, когда связь наладится.`;
export const upNote = (to: Mode) => (to === "video" ? "Связь наладилась: возвращаем обычное качество." : "Связь стала лучше: включаем низкое качество, потом вернём обычное.");
