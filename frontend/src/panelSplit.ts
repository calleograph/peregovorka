/** Правая панель комнаты: верх — транскрипция, низ — чат, между ними перетаскиваемый разделитель. Положение запоминается в браузере. */
const KEY = "pg:panelSplit";
export const DEFAULT_RATIO = 0.65;
export const MIN_RATIO = 0.15;
export const MAX_RATIO = 0.85;

export function clampRatio(r: number): number {
  return Number.isFinite(r) ? Math.min(MAX_RATIO, Math.max(MIN_RATIO, r)) : DEFAULT_RATIO;
}

export function loadRatio(): number {
  try { const v = localStorage.getItem(KEY); return v === null ? DEFAULT_RATIO : clampRatio(Number(v)); } catch { return DEFAULT_RATIO; }
}

export function saveRatio(r: number): void {
  try { localStorage.setItem(KEY, String(Math.round(clampRatio(r) * 1000) / 1000)); } catch { /* не запоминается */ }
}

/** Доля верхней части по положению указателя: y — координата, top/height — рамка области панели. */
export function ratioFromPointer(y: number, top: number, height: number): number {
  return height > 0 ? clampRatio((y - top) / height) : DEFAULT_RATIO;
}

/** Что показывать в каждой части: «свёрнута» — только заголовок, остальное место занимает другая часть. */
export type Collapsed = null | "top" | "bottom";
export function flexFor(part: "top" | "bottom", ratio: number, collapsed: Collapsed): string {
  if (collapsed === part) return "0 0 auto";
  if (collapsed) return "1 1 0";
  return `${part === "top" ? ratio : 1 - ratio} 1 0`;
}
