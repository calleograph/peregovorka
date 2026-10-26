import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Значок кнопки копирования на фоне кнопки — в светлой и тёмной темах не ниже 4,5:1; зелёная и красная кнопки различимы и по цвету, и по яркости.
const css = readFileSync(resolve(__dirname, "linkcopy.css"), "utf8");
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const tokens = (block: string) => Object.fromEntries([...block.matchAll(/--(lc-[a-z-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: dark)")));
const dark = tokens(css.slice(css.indexOf(':root[data-theme="dark"] {'), css.indexOf(".lc-group")));
const media = tokens(css.slice(css.indexOf("@media (prefers-color-scheme: dark)"), css.indexOf(':root[data-theme="dark"] {')));

describe("контраст кнопок копирования ссылок", () => {
  for (const [name, t] of [["светлая тема", light], ["тёмная тема", dark]] as const) {
    it(`${name}: значок на фоне кнопки (в покое и при наведении) — не ниже 4,5:1, граница отличима от фона`, () => {
      for (const k of ["ok", "bad"]) {
        expect(ratio(t[`lc-${k}-fg`], t[`lc-${k}-bg`]), `${k}: покой`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(t[`lc-${k}-fg`], t[`lc-${k}-hover`]), `${k}: наведение`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(t[`lc-${k}-line`], t[`lc-${k}-bg`]), `${k}: граница`).toBeGreaterThanOrEqual(1.15);
      }
    });
  }
  it("тёмная тема по настройке системы и по выбору пользователя — одинаковые значения", () => { expect(media).toEqual(dark); });
  it("кнопки различаются не только цветом: у каждой своя подпись для диктора и подсказка", () => {
    const src = readFileSync(resolve(__dirname, "components/LinkCopy.tsx"), "utf8");
    expect(src).toContain('"Копировать ссылку для сотрудников"');
    expect(src).toContain('"Копировать гостевую ссылку"');
    expect(src).toMatch(/aria-label=\{label\}/);
  });
});
