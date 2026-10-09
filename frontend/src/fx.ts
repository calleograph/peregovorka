import type { PointerEvent as ReactPointerEvent } from "react";

/** Эффекты наведения. Все выключаются при «уменьшить движение» в системе: тогда остаются только цвет и граница. */
export const reducedMotion = (): boolean => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** «Прожектор»: подсветка карточки следует за указателем (CSS-переменные --mx и --my, градиент рисует design.css). */
export function spotlight(e: ReactPointerEvent<HTMLElement>): void {
  if (e.pointerType === "touch" || reducedMotion()) return;
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--mx", `${e.clientX - r.left}px`);
  el.style.setProperty("--my", `${e.clientY - r.top}px`);
}

/** «Магнитная» кнопка: слегка тянется к указателю (смещение — доля расстояния от центра, не больше max пикселей). */
export function magnetMove(e: ReactPointerEvent<HTMLElement>, pull = 0.22, max = 7): void {
  if (e.pointerType === "touch" || reducedMotion()) return;
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  const clamp = (v: number) => Math.max(-max, Math.min(max, v));
  el.style.setProperty("--tx", `${clamp((e.clientX - (r.left + r.width / 2)) * pull)}px`);
  el.style.setProperty("--ty", `${clamp((e.clientY - (r.top + r.height / 2)) * pull)}px`);
}

export function magnetLeave(e: ReactPointerEvent<HTMLElement>): void {
  e.currentTarget.style.setProperty("--tx", "0px");
  e.currentTarget.style.setProperty("--ty", "0px");
}

/** Готовые обработчики для «магнитной» кнопки: <button {...magnet}>. */
export const magnet = { onPointerMove: (e: ReactPointerEvent<HTMLElement>) => magnetMove(e), onPointerLeave: magnetLeave };

/** Цветовая пара для значка комнаты по названию: одно и то же название всегда даёт одни и те же цвета (оттенки подобраны под обе темы). */
export function tint(name: string): { a: number; b: number } {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const a = h % 360;
  return { a, b: (a + 38) % 360 };
}

export function initials(name: string): string {
  const tokens = name.match(/[\p{L}\p{N}]+/gu) ?? [];
  const words = tokens.filter((t) => /\p{L}/u.test(t));          // «ИТ-1 · Планёрка» → «ИП», «Переговорная «Север»» → «ПС»
  const use = words.length > 1 ? words : tokens;
  const letters = use.length > 1 ? use[0][0] + use[1][0] : (use[0] ?? name.trim()).slice(0, 2);
  return letters.toUpperCase();
}
