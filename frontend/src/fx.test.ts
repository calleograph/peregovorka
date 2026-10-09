import { describe, expect, it } from "vitest";
import { initials, tint } from "./fx";

describe("оформление списка комнат", () => {
  it("инициалы значка комнаты берутся из слов названия, а не из знаков препинания", () => {
    expect(initials("ИТ-1 · Планёрка")).toBe("ИП");
    expect(initials("Переговорная «Север»")).toBe("ПС");
    expect(initials("Продажи")).toBe("ПР");
    expect(initials("Room 7")).toBe("R7");
  });
  it("цвет значка зависит только от названия и укладывается в круг оттенков", () => {
    expect(tint("Север")).toEqual(tint("Север"));
    expect(tint("Север").a).not.toBe(tint("Юг").a);
    const t = tint("Разбор инцидента");
    expect(t.a).toBeGreaterThanOrEqual(0); expect(t.a).toBeLessThan(360); expect(t.b).toBeLessThan(360);
  });
});
