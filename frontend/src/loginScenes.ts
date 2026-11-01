/** Решения о фоне страницы входа (components/LoginScenes.tsx): какой режим показывать и когда снижать нагрузку. Отдельно от компонента — проверяется тестами. */

export const SCENES = 4;                 // сеть · совместная работа · волны и орбиты · схема экосистемы
export const SCENE_MS = 10_000;          // сколько держится одна сцена
export const FADE_MS = 2_400;            // плавный переход между сценами (CSS, opacity)

/**
 * animated — компьютер: сцены сменяют друг друга, внутри сцены — мягкое движение;
 * still — та же картинка без движения («уменьшить движение» в системе, экономия трафика, слабый процессор);
 * mobile — телефон и планшет: упрощённая статичная сцена «волны» (минимум элементов, без анимации).
 */
export type SceneMode = "animated" | "still" | "mobile";

export function sceneMode(env: { width: number; coarse: boolean; reduced: boolean; saveData?: boolean; cores?: number }): SceneMode {
  if (env.width < 760 || (env.coarse && env.width < 1400)) return "mobile";
  if (env.reduced || env.saveData || (env.cores ?? 4) <= 2) return "still";
  return "animated";
}

/**
 * Снижение интенсивности по фактической плавности: кадры замеряются короткими окнами (не постоянно), по медиане интервала между кадрами.
 * 0 — полное движение; 1 — спокойнее (только смена сцен и самые лёгкие эффекты); 2 — без движения. Уровень только растёт: фон не «мигает» между режимами.
 */
export class FrameGovernor {
  level = 0;
  private samples: number[] = [];

  push(dtMs: number): void { if (dtMs > 0 && dtMs < 1000) this.samples.push(dtMs); }

  /** Итог окна замера: новый уровень. Пустое или короткое окно (вкладка в фоне) ничего не меняет. */
  settle(): number {
    const s = this.samples.sort((a, b) => a - b);
    this.samples = [];
    if (s.length < 20) return this.level;
    const median = s[Math.floor(s.length / 2)];
    const next = median > 55 ? 2 : median > 30 ? 1 : 0;      // < ~18 кадров/с — стоп; < ~33 кадров/с — спокойнее
    this.level = Math.max(this.level, next);
    return this.level;
  }
}
