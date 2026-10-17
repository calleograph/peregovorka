/**
 * Адаптивное качество исходящего видео с приоритетом звука.
 *
 * Звук — главное: при плохом канале страдает видео, а не речь. Уровни камеры идут от лучшего к худшему: high → medium → low → min → off (видео выключено,
 * остаётся только звук). Решение принимает чистый контроллер по периодическим замерам (качество соединения LiveKit, причина ограничения кодека, потери, задержка).
 * Гистерезис: вниз — после двух плохих замеров подряд (при потере связи сразу на два шага), вверх — только после шести хороших подряд и не раньше паузы после последнего
 * ухудшения, по одному шагу. Ручной выбор отключает автоматику. Демонстрация экрана настраивается отдельно (screenShare.ts) и сюда не входит.
 */
export type VideoLevel = "high" | "medium" | "low" | "min" | "off";
export const LEVELS: VideoLevel[] = ["high", "medium", "low", "min", "off"];

export const LEVEL_LABEL: Record<VideoLevel, string> = { high: "Высокое", medium: "Среднее", low: "Низкое", min: "Минимальное", off: "Только звук" };

/** Параметры верхнего слоя камеры на уровень (нижние simulcast-слои остаются, их потолок ограничивается). */
export interface LevelSpec { maxBitrate: number; maxFramerate: number; scaleDown: number }
export const LEVEL_SPEC: Record<Exclude<VideoLevel, "off">, LevelSpec> = {
  high: { maxBitrate: 1_500_000, maxFramerate: 24, scaleDown: 1 },     // 720p
  medium: { maxBitrate: 600_000, maxFramerate: 15, scaleDown: 2 },     // 360p
  low: { maxBitrate: 250_000, maxFramerate: 10, scaleDown: 4 },        // 180p
  min: { maxBitrate: 100_000, maxFramerate: 5, scaleDown: 4 },
};

export type ConnQuality = "excellent" | "good" | "poor" | "lost" | "unknown";
export interface Sample {
  quality: ConnQuality;
  limited?: string;           // qualityLimitationReason: none | bandwidth | cpu | other
  lossPct?: number;           // потери исходящего потока по отчёту получателя, %
  rttMs?: number;
}

export type Verdict = "good" | "bad" | "lost" | "neutral";

export function judge(s: Sample): Verdict {
  if (s.quality === "lost") return "lost";
  if (s.quality === "poor" || s.limited === "bandwidth" || s.limited === "cpu" || (s.lossPct ?? 0) >= 8 || (s.rttMs ?? 0) >= 600) return "bad";
  if ((s.quality === "excellent" || s.quality === "good") && (s.lossPct ?? 0) < 2 && (s.rttMs ?? 0) < 300) return "good";
  return "neutral";
}

export interface Decision { level: VideoLevel; changed: boolean; reason: "degraded" | "recovered" | "manual" | "none"; verdict: Verdict }

export class AdaptiveController {
  level: VideoLevel;
  private manual: VideoLevel | null = null;
  private bad = 0;
  private good = 0;
  private lastDown = -Infinity;

  constructor(start: VideoLevel = "high", private opts = { badToDrop: 2, goodToRise: 6, cooldownMs: 20_000 }) { this.level = start; }

  get isManual(): boolean { return this.manual !== null; }

  /** Ручной выбор уровня; `null` — вернуть автоматику (она продолжит с текущего уровня). */
  setManual(l: VideoLevel | null): Decision {
    this.manual = l;
    this.bad = this.good = 0;
    if (l === null || l === this.level) return { level: this.level, changed: false, reason: "none", verdict: "neutral" };
    this.level = l;
    return { level: l, changed: true, reason: "manual", verdict: "neutral" };
  }

  push(s: Sample, now: number = Date.now()): Decision {
    const verdict = judge(s);
    if (this.manual) return { level: this.level, changed: false, reason: "none", verdict };
    const i = LEVELS.indexOf(this.level);
    if (verdict === "bad" || verdict === "lost") {
      this.good = 0;
      this.bad += verdict === "lost" ? this.opts.badToDrop : 1;
      if (this.bad >= this.opts.badToDrop && i < LEVELS.length - 1) {
        this.bad = 0;
        this.lastDown = now;
        this.level = LEVELS[Math.min(LEVELS.length - 1, i + (verdict === "lost" ? 2 : 1))];
        return { level: this.level, changed: true, reason: "degraded", verdict };
      }
    } else if (verdict === "good") {
      this.bad = 0;
      this.good += 1;
      if (this.good >= this.opts.goodToRise && i > 0 && now - this.lastDown >= this.opts.cooldownMs) {
        this.good = 0;
        this.level = LEVELS[i - 1];
        return { level: this.level, changed: true, reason: "recovered", verdict };
      }
    } else { this.bad = 0; }          // «нейтрально» не копит ни плохие, ни хорошие замеры
    return { level: this.level, changed: false, reason: "none", verdict };
  }
}

/** Нужно ли камере быть включённой на этом уровне (вернуть камеру при возврате с «Только звук» — только если пользователь сам её не выключал). */
export const cameraAllowed = (l: VideoLevel): l is Exclude<VideoLevel, "off"> => l !== "off";

export interface EncodingLike { rid?: string; maxBitrate?: number; maxFramerate?: number; scaleResolutionDownBy?: number; active?: boolean; priority?: string; networkPriority?: string }

/**
 * Применяет уровень к кодировкам отправителя: верхний слой (наименьший scaleResolutionDownBy) получает параметры уровня, остальные слои — не больше своего потолка.
 * Возвращает новый массив (исходный не меняется). Для одиночной кодировки (без simulcast) применяются те же параметры.
 */
export function applyLevelToEncodings(encodings: EncodingLike[], level: Exclude<VideoLevel, "off">): EncodingLike[] {
  const spec = LEVEL_SPEC[level];
  const ordered = encodings.map((e, idx) => ({ e, idx })).sort((a, b) => (a.e.scaleResolutionDownBy ?? 1) - (b.e.scaleResolutionDownBy ?? 1));
  const out = encodings.map((e) => ({ ...e }));
  ordered.forEach(({ idx }, rank) => {
    if (rank === 0) {
      out[idx].maxBitrate = spec.maxBitrate;
      out[idx].maxFramerate = spec.maxFramerate;
      out[idx].scaleResolutionDownBy = spec.scaleDown;
    } else {
      const cap = Math.max(40_000, Math.floor(spec.maxBitrate / (rank + 1)));
      out[idx].maxBitrate = Math.min(out[idx].maxBitrate ?? cap, cap);
      out[idx].scaleResolutionDownBy = Math.max(out[idx].scaleResolutionDownBy ?? 1, spec.scaleDown * (rank + 1));
    }
    out[idx].priority = "low"; out[idx].networkPriority = "low";            // видео уступает звуку
  });
  return out;
}
