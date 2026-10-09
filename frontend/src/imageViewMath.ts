/** Математика просмотрщика изображений (чистые функции — проверяются тестами). Вид: масштаб `s` и смещение центра изображения (x, y) от центра области просмотра. */
export interface View { s: number; x: number; y: number }

export const MAX_ZOOM = 8;

/** Масштаб «по размеру окна»: большое изображение вписывается целиком, маленькое показывается 1:1 (не растягивается). */
export function fitScale(stageW: number, stageH: number, natW: number, natH: number): number {
  if (!stageW || !stageH || !natW || !natH) return 1;
  return Math.min(1, stageW / natW, stageH / natH);
}

export function clampScale(s: number, fit: number): number {
  return Math.min(MAX_ZOOM, Math.max(Math.min(fit, 1) * 0.5, s));       // отдалять можно до половины «вписанного» размера
}

/** Новый масштаб с сохранением точки под курсором: (cx, cy) — координаты курсора относительно центра области просмотра. */
export function zoomAt(v: View, next: number, cx: number, cy: number, fit: number): View {
  const s = clampScale(next, fit);
  const k = s / v.s;
  return { s, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k };
}

/** Множитель для колеса мыши (deltaMode: 0 — пиксели, 1 — строки). */
export function wheelFactor(deltaY: number, deltaMode = 0): number {
  const px = deltaMode === 1 ? deltaY * 16 : deltaY;
  return Math.exp(-px * 0.0015);
}

/** Не даёт утащить изображение целиком за пределы области: хотя бы `keep` пикселей остаются видны. */
export function clampPan(v: View, stageW: number, stageH: number, natW: number, natH: number, keep = 80): View {
  const w = natW * v.s, h = natH * v.s;
  const maxX = Math.max(0, (w + stageW) / 2 - keep), maxY = Math.max(0, (h + stageH) / 2 - keep);
  return { ...v, x: Math.min(maxX, Math.max(-maxX, v.x)), y: Math.min(maxY, Math.max(-maxY, v.y)) };
}

/** Двойной щелчок: у «вписанного» вида — приблизить (до 100 % или вдвое), иначе вернуть «по размеру окна». */
export function doubleTapTarget(s: number, fit: number): number {
  return s <= fit * 1.05 ? Math.max(1, fit * 2) : fit;
}

export function dist(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Индекс соседнего изображения (по кругу не листаем: на краях остаёмся на месте). */
export function step(i: number, d: number, n: number): number {
  return Math.min(n - 1, Math.max(0, i + d));
}
