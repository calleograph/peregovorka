import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_SITE, THEMES, effectiveTheme, luminance, mix, onColor, palette, type Site } from "./site";
import { pageTitle } from "./pageTitle";

const contrast = (a: string, b: string) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

describe("оформление установки", () => {
  it("стандартное оформление — без фирменных значений, название Peregovorka", () => {
    expect(DEFAULT_SITE.name).toBe("Peregovorka");
    expect(DEFAULT_SITE.customized).toBe(false);
    expect(palette("", "", false)).toBeNull();
  });
  it("фирменный цвет даёт читаемые кнопки: текст на акценте ≥ 4,5:1 в светлой и тёмной теме", () => {
    for (const c of ["#1a56db", "#0f766e", "#b45309", "#f5d90a", "#111111", "#ffffff"]) {
      for (const dark of [false, true]) {
        const p = palette(c, "", dark)!;
        expect(contrast(p.accent, p.text), `${c} dark=${dark}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
  it("в тёмной теме акцент осветляется, чтобы не терялся на тёмном фоне", () => {
    expect(luminance(palette("#0b2a6b", "", true)!.accent)).toBeGreaterThan(luminance("#0b2a6b"));
  });
  it("некорректный цвет игнорируется, а не ломает страницу", () => {
    expect(palette("red", "", false)).toBeNull();
    expect(mix("zzz", "#ffffff", 0.5)).toBe("zzz");
    expect(onColor("#ffffff")).toBe("#0a1226");
  });
  it("тема: выбор пользователя важнее темы администратора, та — важнее системной", () => {
    const s = (theme: Site["theme"]): Site => ({ ...DEFAULT_SITE, theme });
    expect(effectiveTheme(s("system"), "system")).toBe("system");
    expect(effectiveTheme(s("dark"), "system")).toBe("dark");
    expect(effectiveTheme(s("dark"), "amber")).toBe("amber");
    expect(effectiveTheme(s("light"), "corporate")).toBe("corporate");
  });
  it("темы: по умолчанию «как в системе», идентификаторы уникальны, цветные темы описаны в стилях", () => {
    expect(THEMES[0].id).toBe("system");
    expect(new Set(THEMES.map((t) => t.id)).size).toBe(THEMES.length);
    expect(THEMES.length).toBe(6);
    const css = ["styles.css", "design.css"].map((f) => readFileSync(resolve(__dirname, f), "utf8")).join("\n");
    for (const t of THEMES.filter((x) => x.id !== "system" && x.id !== "light")) expect(css, t.id).toContain(`:root[data-theme="${t.id}"]`);
  });
  it("пастельные темы: текст на кнопке и основной текст читаемы (≥ 4,5:1)", () => {
    const css = readFileSync(resolve(__dirname, "design.css"), "utf8");
    for (const id of ["diamond", "amber", "corporate"]) {
      const block = css.slice(css.indexOf(`:root[data-theme="${id}"]`)).split("}")[0];
      const v = (n: string) => block.match(new RegExp(`--${n}:\\s*(#[0-9a-fA-F]{6})`))![1];
      expect(contrast(v("accent"), v("accent-text")), `${id}: кнопка`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(v("bg"), v("text")), `${id}: текст`).toBeGreaterThanOrEqual(7);
      expect(contrast(v("surface"), v("muted")), `${id}: вторичный текст`).toBeGreaterThanOrEqual(4.5);
    }
  });
  it("заголовок вкладки берёт название системы из настроек сайта", () => {
    expect(pageTitle("/", true, "Встречи компании")).toBe("Переговорки · Встречи компании");
    expect(pageTitle("/legal/terms", false, "Встречи компании")).toBe("Документ · Встречи компании");
    expect(pageTitle("/")).toBe("Переговорки · Peregovorka");
  });
});
