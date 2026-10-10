import { describe, expect, it } from "vitest";
import { SPEEDS, fmtTime, keyAction, mediaErrorText, nextSpeed } from "./mediaPlayerMath";

describe("плеер записей: чистая логика", () => {
  it("время форматируется без секунд-«мусора»", () => {
    expect(fmtTime(5)).toBe("0:05");
    expect(fmtTime(65)).toBe("1:05");
    expect(fmtTime(3725)).toBe("1:02:05");
    expect(fmtTime(NaN)).toBe("0:00");
    expect(fmtTime(Infinity)).toBe("0:00");
    expect(fmtTime(-3)).toBe("0:00");
  });
  it("скорости — 0,75; 1; 1,25; 1,5; 2 и не выходят за края", () => {
    expect([...SPEEDS]).toEqual([0.75, 1, 1.25, 1.5, 2]);
    expect(nextSpeed(1, 1)).toBe(1.25);
    expect(nextSpeed(1, -1)).toBe(0.75);
    expect(nextSpeed(2, 1)).toBe(2);
    expect(nextSpeed(0.75, -1)).toBe(0.75);
    expect(nextSpeed(3, 1)).toBe(1.25);       // неизвестное значение считается обычной скоростью
  });
  it("клавиши: пауза, перемотка, громкость, звук, экран, скорость; с модификаторами — не перехватываются", () => {
    expect(keyAction({ key: " " })).toEqual({ type: "toggle" });
    expect(keyAction({ key: "ArrowRight" })).toEqual({ type: "seek", by: 5 });
    expect(keyAction({ key: "ArrowLeft", shiftKey: true })).toEqual({ type: "seek", by: -30 });
    expect(keyAction({ key: "ArrowUp" })).toEqual({ type: "volume", by: 0.1 });
    expect(keyAction({ key: "m" })).toEqual({ type: "mute" });
    expect(keyAction({ key: "f" })).toEqual({ type: "fullscreen" });
    expect(keyAction({ key: "." })).toEqual({ type: "speed", dir: 1 });
    expect(keyAction({ key: "ArrowRight", ctrlKey: true })).toBeNull();
    expect(keyAction({ key: "Escape" })).toBeNull();
    expect(keyAction({ key: "a" })).toBeNull();
  });
  it("ошибки доступа к записи — понятным языком, без технических подробностей", () => {
    expect(mediaErrorText(410)).toContain("удалён");
    expect(mediaErrorText(503)).toContain("недоступно");
    expect(mediaErrorText(404)).toContain("нет доступа");
    expect(mediaErrorText(409, "Запись ещё формируется.")).toBe("Запись ещё формируется.");
    expect(mediaErrorText(500)).toContain("Не удалось загрузить");
  });
});
