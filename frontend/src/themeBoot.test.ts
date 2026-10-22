import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("тема до загрузки приложения", () => {
  it("theme-boot.js подключён в index.html до приложения и читает тот же ключ, что site.ts", () => {
    const html = readFileSync(new URL("../index.html", import.meta.url), "utf-8");
    const boot = readFileSync(new URL("../public/theme-boot.js", import.meta.url), "utf-8");
    const site = readFileSync(new URL("./site.ts", import.meta.url), "utf-8");
    expect(html.indexOf('src="/theme-boot.js"')).toBeGreaterThan(-1);
    expect(html.indexOf('src="/theme-boot.js"')).toBeLessThan(html.indexOf("/src/main.tsx"));
    const key = /THEME_KEY = "([^"]+)"/.exec(site)![1];
    expect(boot).toContain(`"${key}"`);
    // в боевом файле нет ничего, кроме выставления атрибута (CSP: свой скрипт, без eval)
    expect(boot).not.toMatch(/eval|innerHTML|fetch/);
  });
  it("форма входа не использует фиксированных цветов, только токены темы", () => {
    const css = readFileSync(new URL("./design.css", import.meta.url), "utf-8");
    const block = css.split("\n").filter((l) => /^\.px-(shell|form|tag|welcome)|^\.login-px/.test(l)).join("\n");
    expect(block).not.toMatch(/#[0-9a-f]{6}/i);
  });
});
