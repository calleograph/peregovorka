import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Значок кнопки копирования на карточке — в светлой и тёмной темах не ниже 4,5:1 (в покое и при наведении, когда оттенок гуще); сплошных тёмных заливок нет.
const css = readFileSync(resolve(__dirname, "linkcopy.css"), "utf8");
type RGB = [number, number, number];
const rgb = (hex: string): RGB => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB;
const lum = ([r, g, b]: RGB) => { const c = [r, g, b].map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const ratio = (a: RGB, b: RGB) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const over = (fg: RGB, alpha: number, bg: RGB): RGB => fg.map((v, i) => Math.round(v * alpha + bg[i] * (1 - alpha))) as RGB;
const tokens = (block: string) => Object.fromEntries([...block.matchAll(/--(lc-[a-z-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: dark)")));
const dark = tokens(css.slice(css.indexOf(':root[data-theme="dark"] {'), css.indexOf(".lc-group")));
const media = tokens(css.slice(css.indexOf("@media (prefers-color-scheme: dark)"), css.indexOf(':root[data-theme="dark"] {')));
const CARD: Record<string, RGB[]> = { light: [rgb("#ffffff"), rgb("#f3f5f8")], dark: [rgb("#161b24"), rgb("#0e1218")] };

describe("кнопки копирования ссылок: контрастность и стиль", () => {
  for (const [name, t, cards] of [["светлая тема", light, CARD.light], ["тёмная тема", dark, CARD.dark]] as const) {
    it(`${name}: значок на карточке и на оттенке наведения — не ниже 4,5:1, рамка отличима от карточки`, () => {
      for (const k of ["ok", "bad"]) for (const card of cards) {
        const fg = rgb(t[`lc-${k}-fg`]);
        expect(ratio(fg, over(fg, 0.07, card)), `${k}: покой`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(fg, over(fg, 0.14, card)), `${k}: наведение`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(rgb(t[`lc-${k}-line`]), card), `${k}: рамка`).toBeGreaterThanOrEqual(1.4);
      }
    });
  }
  it("тёмная тема по настройке системы и по выбору пользователя — одинаковые значения", () => { expect(media).toEqual(dark); });
  it("стиль контурный: заливка — лишь прозрачный оттенок значка, сплошных тёмных заливок нет", () => {
    expect(css).toMatch(/\.iconbtn\.lc-staff \{[^}]*background: color-mix\(in srgb, var\(--lc-ok-fg\) 7%, transparent\)/);
    expect(css).toMatch(/\.iconbtn\.lc-guest \{[^}]*background: color-mix\(in srgb, var\(--lc-bad-fg\) 7%, transparent\)/);
    expect(css).not.toMatch(/--lc-(ok|bad)-bg/);
  });
  it("кнопки различаются не только цветом: у каждой своя подпись для диктора и подсказка", () => {
    const src = readFileSync(resolve(__dirname, "components/LinkCopy.tsx"), "utf8");
    expect(src).toContain('"Копировать ссылку для сотрудников"');
    expect(src).toContain('"Копировать гостевую ссылку"');
    expect(src).toMatch(/aria-label=\{label\}/);
  });
});
