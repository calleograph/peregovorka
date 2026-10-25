import { describe, expect, it } from "vitest";
import { isCrystalsKey } from "./crystalsHotkey";

const ev = (o: Partial<KeyboardEvent>) => ({ code: "KeyM", key: "m", altKey: true, ctrlKey: false, shiftKey: false, metaKey: false, isComposing: false, ...o });

describe("сочетание Alt+M", () => {
  it("работает по физической клавише в любой раскладке", () => {
    expect(isCrystalsKey(ev({}))).toBe(true);
    expect(isCrystalsKey(ev({ key: "ь" }))).toBe(true);
  });
  it("без кода клавиши смотрит символ: m или ь", () => {
    expect(isCrystalsKey(ev({ code: "", key: "M" }))).toBe(true);
    expect(isCrystalsKey(ev({ code: "", key: "ь" }))).toBe(true);
    expect(isCrystalsKey(ev({ code: "", key: "x" }))).toBe(false);
  });
  it("не трогает сочетания с Ctrl, Shift, Meta, без Alt и ввод через IME", () => {
    for (const o of [{ ctrlKey: true }, { shiftKey: true }, { metaKey: true }, { altKey: false }, { isComposing: true }, { code: "KeyN", key: "n" }]) expect(isCrystalsKey(ev(o))).toBe(false);
  });
});
