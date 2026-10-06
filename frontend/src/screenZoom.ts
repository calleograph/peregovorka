/**
 * Масштабирование и перемещение чужого экрана (как в просмотрщиках изображений): колесо — приближение к точке под курсором,
 * перетаскивание — сдвиг. Чистая математика без DOM — проверяется модульными тестами.
 *
 * Модель: видеоэлемент занимает прямоугольник сцены (W×H), увеличенный в `s` раз и сдвинутый на (x, y) относительно левого верхнего угла
 * сцены. Изменяется именно размер элемента, а не CSS-`scale`: так adaptiveStream видит реальный размер и запрашивает слой высокого качества.
 *  - уменьшить меньше исходного размера (s < 1) нельзя; при s = 1 картинка всегда вписана, сдвиг сброшен;
 *  - приблизить можно до `maxScale` — не бесконечно и не дальше, чем есть смысл (пиксели источника, ×2 запаса);
 *  - сдвиг ограничен краями изображения: за пределы кадра («чёрные поля» сверх letterbox) уйти нельзя.
 */
export interface View { s: number; x: number; y: number }
export interface Size { w: number; h: number }
export interface Rect { x: number; y: number; w: number; h: number }

export const MIN_SCALE = 1;
export const SCALE_FLOOR = 3;      // даже для маленького источника можно приблизить хотя бы втрое
export const SCALE_CEIL = 10;      // и никогда больше, чем в 10 раз
export const IDENTITY: View = { s: 1, x: 0, y: 0 };

/** Прямоугольник самого изображения внутри сцены при s = 1 (object-fit: contain или cover). */
export function contentRect(stage: Size, video: Size, fill: boolean): Rect {
  if (!stage.w || !stage.h || !video.w || !video.h) return { x: 0, y: 0, w: stage.w, h: stage.h };
  const k = fill ? Math.max(stage.w / video.w, stage.h / video.h) : Math.min(stage.w / video.w, stage.h / video.h);
  const w = video.w * k, h = video.h * k;
  return { x: (stage.w - w) / 2, y: (stage.h - h) / 2, w, h };
}

/** Предел приближения: до двукратного «пиксель к пикселю» источника, но не меньше ×3 и не больше ×10. */
export function maxScale(stage: Size, video: Size, fill: boolean): number {
  const r = contentRect(stage, video, fill);
  if (!video.w || !r.w) return 6;
  return Math.min(SCALE_CEIL, Math.max(SCALE_FLOOR, (2 * video.w) / r.w));
}

function clampAxis(offset: number, stage: number, c0: number, c: number, s: number): number {
  const size = c * s;
  if (size <= stage + 0.5) return (stage - size) / 2 - c0 * s;                 // изображение целиком помещается — по центру
  const left = Math.min(0, Math.max(stage - size, offset + c0 * s));          // край кадра не отрывается от края сцены
  return left - c0 * s;
}

/** Приводит вид к допустимому: масштаб в [1, max], сдвиг — внутри кадра. */
export function clampView(v: View, stage: Size, video: Size, fill: boolean): View {
  const s = Math.min(maxScale(stage, video, fill), Math.max(MIN_SCALE, v.s));
  if (s <= MIN_SCALE + 1e-6) return { ...IDENTITY };
  const r = contentRect(stage, video, fill);
  return { s, x: clampAxis(v.x, stage.w, r.x, r.w, s), y: clampAxis(v.y, stage.h, r.y, r.h, s) };
}

/** Изменить масштаб в `factor` раз так, чтобы точка (cx, cy) сцены осталась под курсором. */
export function zoomAt(v: View, factor: number, cx: number, cy: number, stage: Size, video: Size, fill: boolean): View {
  const target = Math.min(maxScale(stage, video, fill), Math.max(MIN_SCALE, v.s * factor));
  const k = target / v.s;
  return clampView({ s: target, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k }, stage, video, fill);
}

export function panBy(v: View, dx: number, dy: number, stage: Size, video: Size, fill: boolean): View {
  return clampView({ ...v, x: v.x + dx, y: v.y + dy }, stage, video, fill);
}

/** Коэффициент масштаба из события колеса: плавный (экспонента), одинаков для «вперёд» и «назад», ограничен за один шаг. */
export function wheelFactor(deltaY: number, deltaMode = 0, ctrlKey = false): number {
  const px = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 400 : deltaY;
  const clamped = Math.max(-300, Math.min(300, px));
  return Math.exp(-clamped * (ctrlKey ? 0.01 : 0.0015));   // ctrl — жест «щипок» на тачпаде: приходит мелкими шагами
}

export function percent(v: View): string { return `${Math.round(v.s * 100)}%`; }
