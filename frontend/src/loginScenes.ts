/** Решения о фоне страницы входа (components/LoginScenes.tsx): какой режим показывать, когда снижать нагрузку, форма волн. Отдельно от компонента — проверяется тестами. */

/**
 * animated — компьютер: мягкое движение волн, света и точек на орбитах;
 * still — та же картинка без движения («уменьшить движение» в системе, экономия трафика, слабый процессор);
 * mobile — телефон и планшет: та же композиция проще (без сетки) и без движения.
 */
export type SceneMode = "animated" | "still" | "mobile";

export function sceneMode(env: { width: number; coarse: boolean; reduced: boolean; saveData?: boolean; cores?: number }): SceneMode {
  if (env.width < 760 || (env.coarse && env.width < 1400)) return "mobile";
  if (env.reduced || env.saveData || (env.cores ?? 4) <= 2) return "still";
  return "animated";
}

/**
 * Снижение интенсивности по фактической плавности: кадры замеряются короткими окнами (не постоянно), по медиане интервала между кадрами.
 * 0 — полное движение; 1 — без движения крупных слоёв (волны, свет: без видеокарты их перенос на экран стоит дороже всего), мелкие точки и искры живут;
 * 2 — без движения. Очень медленно — сразу стоп; умеренно — на ступень ниже и новый замер. Уровень только растёт: фон не «мигает» между режимами.
 */
export class FrameGovernor {
  level = 0;
  private samples: number[] = [];

  push(dtMs: number): void { if (dtMs > 0 && dtMs < 1000) this.samples.push(dtMs); }

  /** Итог окна замера: новый уровень. Пустое или короткое окно (вкладка в фоне) ничего не меняет. */
  settle(): number {
    const s = this.samples.sort((a, b) => a - b);
    this.samples = [];
    if (s.length < 8) return this.level;            // вкладка в фоне или окно почти без кадров; 6 кадров/с за 2 с — уже 12 замеров
    const median = s[Math.floor(s.length / 2)];
    // < ~18 кадров/с — стоп (без видеокарты дорог каждый кадр: на экране много полупрозрачных слоёв); < ~33 кадров/с — на ступень спокойнее
    this.level = median > 55 ? 2 : median > 30 ? Math.min(2, this.level + 1) : this.level;
    return this.level;
  }
}

/**
 * Волна для фона: сумма двух синусоид с периодом period (вторая — вдвое чаще), повторённая periods раз. Начало и конец совпадают по высоте и наклону,
 * поэтому две волны подряд, сдвинутые на период, текут без шва. Ломаная из 64 отрезков на период — при растяжении на ширину окна выглядит гладкой.
 */
export function wavePath(period: number, base: number, amp: [number, number], phase: number, periods = 1): string {
  const steps = 64 * periods, out: string[] = [];
  for (let i = 0; i <= steps; i++) {
    const x = (i / steps) * period * periods, t = (2 * Math.PI * x) / period;
    const y = base + amp[0] * Math.sin(t + phase) + amp[1] * Math.sin(2 * t + phase * 1.7);
    out.push(`${i ? "L" : "M"}${+x.toFixed(1)} ${+y.toFixed(1)}`);
  }
  return out.join("");
}
