import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Контраст текста карточек календаря на каждом фоне в светлой и тёмной темах (WCAG AA для обычного текста — 4,5:1). Пастельная тема использует светлые значения.
const css = readFileSync(resolve(__dirname, "calendar.css"), "utf8");
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
function tokens(block: string): Record<string, string> { return Object.fromEntries([...block.matchAll(/--(c-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]])); }
const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: dark)")));
const dark = tokens(css.slice(css.indexOf(':root[data-theme="dark"] {'), css.indexOf(".t-meeting {")));
const media = tokens(css.slice(css.indexOf("@media (prefers-color-scheme: dark)"), css.indexOf(':root[data-theme="dark"] {')));

describe("контрастность цветов календаря", () => {
  for (const [name, t] of [["светлая тема", light], ["тёмная тема", dark]] as const) {
    it(`${name}: текст и значки на каждом фоне карточки — не ниже 4,5:1`, () => {
      for (const tone of ["meeting", "series", "plain", "cancelled"]) {
        expect(ratio(t["c-fg"], t[`c-${tone}-bg`]), `${tone}: основной текст`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(t["c-fg2"], t[`c-${tone}-bg`]), `${tone}: время`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(t[`c-${tone}-bar`], t[`c-${tone}-bg`]), `${tone}: полоса отличима от фона`).toBeGreaterThanOrEqual(3);
      }
      expect(ratio(t["c-conf-fg"], t["c-conf-bg"]), "значок конференции").toBeGreaterThanOrEqual(4.5);
    });
  }
  it("тёмная тема по настройке системы и по выбору пользователя — одинаковые значения", () => { expect(media).toEqual(dark); });
  it("цвета четырёх типов различимы между собой в обеих темах", () => {
    for (const t of [light, dark]) {
      const bars = ["meeting", "series", "plain", "cancelled"].map((k) => t[`c-${k}-bar`]);
      expect(new Set(bars).size).toBe(4);
    }
  });
});
