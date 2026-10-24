// Положение главного окна между запусками: после смены мониторов окно не должно оказаться за пределами доступных экранов.

export type Rect = { x: number; y: number; width: number; height: number };

export const MIN_SIZE = { width: 940, height: 600 };
export const DEFAULT_SIZE = { width: 1366, height: 860 };

function overlap(a: Rect, b: Rect): number {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

/** Сохранённый прямоугольник → прямоугольник на одном из рабочих пространств (без панели задач). Если заголовок окна не виден хотя бы на 1/4 ширины
 *  на каком-то экране (монитор отключён, разрешение уменьшилось) — окно по центру основного экрана. Размер ужимается до рабочей области. */
export function fitBounds(saved: Partial<Rect> | null | undefined, workAreas: Rect[], primary: Rect): Rect {
  const center = (area: Rect, w: number, h: number): Rect => {
    const width = Math.min(w, area.width), height = Math.min(h, area.height);
    return { x: Math.round(area.x + (area.width - width) / 2), y: Math.round(area.y + (area.height - height) / 2), width, height };
  };
  const ok = (n: unknown) => typeof n === "number" && Number.isFinite(n);
  if (!saved || !ok(saved.x) || !ok(saved.y) || !ok(saved.width) || !ok(saved.height)) {
    return center(primary, Math.max(MIN_SIZE.width, Math.min(DEFAULT_SIZE.width, primary.width - 80)), Math.max(MIN_SIZE.height, Math.min(DEFAULT_SIZE.height, primary.height - 60)));
  }
  const r: Rect = { x: Math.round(saved.x!), y: Math.round(saved.y!), width: Math.max(MIN_SIZE.width, Math.round(saved.width!)), height: Math.max(MIN_SIZE.height, Math.round(saved.height!)) };
  const title: Rect = { x: r.x, y: r.y, width: r.width, height: 32 };
  let best: Rect | null = null, bestArea = 0;
  for (const a of workAreas) {
    const o = overlap(title, a);
    if (o > bestArea) { bestArea = o; best = a; }
  }
  if (!best || bestArea < (r.width * 32) / 4) return center(primary, r.width, r.height);
  const width = Math.min(r.width, best.width), height = Math.min(r.height, best.height);
  const x = Math.min(Math.max(r.x, best.x), best.x + best.width - width);
  const y = Math.min(Math.max(r.y, best.y), best.y + best.height - height);
  return { x, y, width, height };
}
