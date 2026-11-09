/**
 * Автоподстройка показа экрана у выступающего: канал или процессор не тянет выбранный профиль — качество показа снижается по ступеням (битрейт, частота кадров, разрешение),
 * а когда связь держится стабильно, так же по ступеням возвращается. Звук при этом не страдает, а эфир и зрители продолжают идти: страдает только чёткость картинки.
 *
 * Ступень 0 — как выбран профиль показа (sharp, balanced, motion, eco). Остальные считаются от него, поэтому любой профиль снижается пропорционально.
 * Вниз: два плохих замера подряд (интервал 5 с), при потере связи сразу на две ступени. Вверх: шесть хороших подряд (≈30 с) и не раньше паузы после последнего снижения.
 * Если после подъёма снова стало плохо в течение полутора минут, следующий подъём требует вдвое больше спокойных замеров (до ≈4 минут): картинка не «качается» туда-сюда.
 * Пять минут без снижений прощают прежние неудачи.
 *
 * Модуль чистый (без DOM и таймеров): время приходит параметром.
 */
import { judge, type Sample, type Verdict } from "./adaptive";

export interface Step { br: number; fps: number; scale: number }
/** Множители от параметров профиля: битрейт, частота кадров, во сколько раз меньше разрешение. */
export const STEPS: Step[] = [
  { br: 1, fps: 1, scale: 1 },
  { br: 0.65, fps: 0.8, scale: 1.33 },
  { br: 0.4, fps: 0.6, scale: 2 },
  { br: 0.22, fps: 0.4, scale: 3 },
];
export const MAX_LEVEL = STEPS.length - 1;
export const MIN_BITRATE = 150_000;
export const MIN_FPS = 4;

/** Параметры отправителя до снижения (то, что поставил профиль). */
export interface Base { maxBitrate?: number; maxFramerate?: number; scale: number }
export interface Params { maxBitrate: number; maxFramerate?: number; scaleResolutionDownBy: number }

/** Параметры первого (верхнего) слоя на ступени. Без заданного потолка битрейта отсчёт идёт от 2,5 Мбит/с. */
export function ladderParams(level: number, base: Base): Params {
  const s = STEPS[Math.max(0, Math.min(MAX_LEVEL, level))];
  const br = base.maxBitrate ?? 2_500_000;
  return {
    maxBitrate: level === 0 ? br : Math.max(MIN_BITRATE, Math.round(br * s.br)),
    ...(base.maxFramerate ? { maxFramerate: level === 0 ? base.maxFramerate : Math.max(MIN_FPS, Math.round(base.maxFramerate * s.fps)) } : {}),
    scaleResolutionDownBy: base.scale * s.scale,
  };
}

export interface LadderDecision { level: number; changed: boolean; reason: "degraded" | "recovered" | "none"; verdict: Verdict }

export class ScreenLadder {
  level = 0;
  private bad = 0;
  private good = 0;
  private lastDown = -Infinity;
  private lastRise = -Infinity;
  private riseNeed: number;

  constructor(private opts = { badToDrop: 2, goodToRise: 6, maxGood: 48, cooldownMs: 20_000, flapMs: 90_000, forgiveMs: 300_000 }) { this.riseNeed = opts.goodToRise; }

  reset(): void { this.level = 0; this.bad = this.good = 0; this.lastDown = this.lastRise = -Infinity; this.riseNeed = this.opts.goodToRise; }

  push(s: Sample, now: number = Date.now()): LadderDecision {
    const verdict = judge(s);
    if (verdict === "bad" || verdict === "lost") {
      this.good = 0;
      this.bad += verdict === "lost" ? this.opts.badToDrop : 1;
      if (this.bad >= this.opts.badToDrop && this.level < MAX_LEVEL) {
        this.bad = 0;
        if (now - this.lastRise < this.opts.flapMs) this.riseNeed = Math.min(this.opts.maxGood, this.riseNeed * 2);       // поднялись и сразу сорвались: ждём дольше
        this.lastDown = now;
        this.level = Math.min(MAX_LEVEL, this.level + (verdict === "lost" ? 2 : 1));
        return { level: this.level, changed: true, reason: "degraded", verdict };
      }
      return { level: this.level, changed: false, reason: "none", verdict };
    }
    this.bad = 0;
    if (verdict === "good") {
      if (now - this.lastDown > this.opts.forgiveMs) this.riseNeed = this.opts.goodToRise;
      this.good += 1;
      if (this.level > 0 && this.good >= this.riseNeed && now - this.lastDown >= this.opts.cooldownMs) {
        this.good = 0;
        this.lastRise = now;
        this.level -= 1;
        return { level: this.level, changed: true, reason: "recovered", verdict };
      }
    }
    return { level: this.level, changed: false, reason: "none", verdict };
  }
}

/** Что сказать выступающему: спокойно, что случилось, чем это не грозит и что вернётся само. */
export function ladderNote(reason: "degraded" | "recovered", level: number, shown: string): string {
  if (reason === "degraded") return `Канал не тянет показ экрана, поэтому качество показа снижено (${shown}). Звук и зрители не страдают. Вернём качество само, когда связь станет стабильной.`;
  return level === 0 ? "Связь стабильна: показ экрана снова в полном качестве." : `Связь стабильна: возвращаем качество показа экрана (${shown}).`;
}
