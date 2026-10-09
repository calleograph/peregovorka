/** Математика фона страницы входа (спокойная геометрическая сетка, реагирующая на курсор). Отдельно от компонента, чтобы проверяться тестами. */

export interface GridSpec { cols: number; rows: number; step: number; ox: number; oy: number }

/** Сетка узлов с запасом за краями окна (линии не обрываются у границы); центрируется по ширине. */
export function gridSpec(w: number, h: number, step: number): GridSpec {
  const cols = Math.ceil(w / step) + 2, rows = Math.ceil(h / step) + 2;
  return { cols, rows, step, ox: (w - (cols - 1) * step) / 2, oy: (h - (rows - 1) * step) / 2 };
}

/** Смещение узла от курсора: узлы мягко «расступаются» в радиусе `radius`, сила спадает плавно (квадратично) и в центре, и у края — без резких границ. */
export function pointerPush(nx: number, ny: number, px: number, py: number, radius: number, strength: number): [number, number] {
  const dx = nx - px, dy = ny - py;
  const d = Math.hypot(dx, dy);
  if (d >= radius || d < 0.001) return [0, 0];
  const k = (1 - d / radius) ** 2 * strength;
  return [(dx / d) * k, (dy / d) * k];
}

/** Медленное «дыхание» узла: малая амплитуда, разные фазы у разных узлов; `energy` (0…1) гасит движение, когда пользователь ничего не делает. */
export function drift(phase: number, tMs: number, amp: number, energy: number): [number, number] {
  const a = amp * Math.max(0, Math.min(1, energy));
  return [Math.sin(tMs / 2600 + phase) * a, Math.cos(tMs / 3100 + phase * 1.7) * a];
}

/** Затухание «энергии»: после последнего движения курсора линейно до нуля за `settleMs`. */
export function energyAt(sinceActivityMs: number, settleMs: number): number {
  return Math.max(0, Math.min(1, 1 - sinceActivityMs / settleMs));
}

/** Плотность сетки: на слабых устройствах (мало ядер) — реже узлы и меньше кадров в секунду. */
export function qualityFor(cores: number | undefined, width: number): { step: number; fps: number } {
  const weak = (cores ?? 4) <= 2;
  const step = weak ? 84 : width < 700 ? 64 : 56;
  return { step, fps: weak ? 20 : 30 };
}
