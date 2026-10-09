import { describe, expect, it } from "vitest";
import { clampRatio, DEFAULT_RATIO, flexFor, MAX_RATIO, MIN_RATIO, ratioFromPointer } from "./panelSplit";
import { COALESCE_MS, shouldPlay } from "./chatSound";

describe("правая панель: разделитель транскрипции и чата", () => {
  it("доля ограничена, мусор заменяется значением по умолчанию", () => {
    expect(clampRatio(0.5)).toBe(0.5);
    expect(clampRatio(0)).toBe(MIN_RATIO);
    expect(clampRatio(5)).toBe(MAX_RATIO);
    expect(clampRatio(Number.NaN)).toBe(DEFAULT_RATIO);
    expect(DEFAULT_RATIO).toBeCloseTo(0.65);
  });
  it("положение указателя → доля верхней части", () => {
    expect(ratioFromPointer(300, 100, 400)).toBeCloseTo(0.5);
    expect(ratioFromPointer(0, 100, 400)).toBe(MIN_RATIO);
    expect(ratioFromPointer(900, 100, 400)).toBe(MAX_RATIO);
    expect(ratioFromPointer(300, 100, 0)).toBe(DEFAULT_RATIO);
  });
  it("свёрнутая часть занимает только заголовок, вторая — всё остальное", () => {
    expect(flexFor("top", 0.65, null)).toBe("0.65 1 0");
    expect(flexFor("bottom", 0.65, null)).toBe("0.35 1 0");
    expect(flexFor("top", 0.65, "top")).toBe("0 0 auto");
    expect(flexFor("bottom", 0.65, "top")).toBe("1 1 0");
  });
});

describe("звук нового сообщения", () => {
  it("серия сообщений даёт один сигнал; выключенный звук молчит", () => {
    expect(shouldPlay(10_000, 0, true)).toBe(true);
    expect(shouldPlay(10_000 + COALESCE_MS - 1, 10_000, true)).toBe(false);
    expect(shouldPlay(10_000 + COALESCE_MS, 10_000, true)).toBe(true);
    expect(shouldPlay(99_999, 0, false)).toBe(false);
  });
});
