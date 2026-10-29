import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ANDROID_RELEASE_URL, ANDROID_VERSION } from "./androidDist";

describe("ссылка на приложение для Android в подвале", () => {
  it("версия совпадает с mobile/android/VERSION, адрес — страница этого выпуска", () => {
    const v = readFileSync(resolve(__dirname, "../../mobile/android/VERSION"), "utf8").trim();
    expect(ANDROID_VERSION).toBe(v);
    expect(ANDROID_RELEASE_URL).toBe(`https://github.com/calleograph/peregovorka/releases/tag/android-v${v}`);
  });
  it("README показывает тот же выпуск", () => {
    expect(readFileSync(resolve(__dirname, "../../README.md"), "utf8")).toContain(`(${ANDROID_RELEASE_URL})`);
  });
  it("в подвале есть ссылка рядом с приложением для Windows", () => {
    const src = readFileSync(resolve(__dirname, "components/ProductInfo.tsx"), "utf8");
    expect(src).toMatch(/DESKTOP_SETUP_URL[\s\S]*ANDROID_RELEASE_URL/);
  });
});
