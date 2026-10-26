import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { DESKTOP_SETUP_URL, DESKTOP_VERSION } from "./desktopDist";

describe("ссылка на приложение для Windows в подвале", () => {
  it("версия совпадает с desktop/package.json (иначе ссылка устарела), адрес — установщик этого выпуска", () => {
    const pkg = JSON.parse(readFileSync(resolve(__dirname, "../../desktop/package.json"), "utf8"));
    expect(DESKTOP_VERSION).toBe(pkg.version);
    expect(DESKTOP_SETUP_URL).toBe(`https://github.com/calleograph/peregovorka/releases/download/desktop-v${pkg.version}/Peregovorka-Setup-${pkg.version}.exe`);
  });
  it("README показывает тот же установщик", () => {
    expect(readFileSync(resolve(__dirname, "../../README.md"), "utf8")).toContain(`[Peregovorka-Setup-${DESKTOP_VERSION}.exe](${DESKTOP_SETUP_URL})`);
  });
  it("в подвале ссылка скрыта внутри самого приложения Windows", () => {
    const src = readFileSync(resolve(__dirname, "components/ProductInfo.tsx"), "utf8");
    expect(src).toMatch(/!desktop && <>[\s\S]*DESKTOP_SETUP_URL/);
  });
});
