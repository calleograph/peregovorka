import { beforeEach, describe, expect, it, vi } from "vitest";
import { confirmLeave, hasDirty, setDirty } from "./dirtyGuard";

describe("защита от потери несохранённых изменений", () => {
  beforeEach(() => { setDirty("a", false); setDirty("b", false); });

  it("без правок уходить можно, вопрос не задаётся", () => {
    const ask = vi.fn(() => false);
    expect(confirmLeave(ask)).toBe(true);
    expect(ask).not.toHaveBeenCalled();
  });

  it("с правками спрашивает; отказ оставляет правки, согласие сбрасывает отметки", () => {
    setDirty("a", true);
    expect(hasDirty()).toBe(true);
    expect(confirmLeave(() => false)).toBe(false);
    expect(hasDirty()).toBe(true);
    expect(confirmLeave(() => true)).toBe(true);
    expect(hasDirty()).toBe(false);
  });

  it("несколько форм: отметка снимается у каждой отдельно", () => {
    setDirty("a", true); setDirty("b", true); setDirty("a", false);
    expect(hasDirty()).toBe(true);
    setDirty("b", false);
    expect(hasDirty()).toBe(false);
  });
});
