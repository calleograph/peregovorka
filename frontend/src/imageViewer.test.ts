import { describe, expect, it } from "vitest";
import { clampPan, clampScale, doubleTapTarget, fitScale, MAX_ZOOM, step, wheelFactor, zoomAt } from "./imageViewMath";
import { placeMenu, shouldKeepNative } from "./menuPlace";
import { transcriptCopyText } from "./transcript";

describe("просмотрщик изображений: масштаб и сдвиг", () => {
  it("большое изображение вписывается, маленькое остаётся 1:1", () => {
    expect(fitScale(800, 600, 4000, 3000)).toBeCloseTo(0.2);
    expect(fitScale(800, 600, 200, 100)).toBe(1);
    expect(fitScale(0, 0, 10, 10)).toBe(1);
  });
  it("масштаб ограничен сверху и снизу", () => {
    expect(clampScale(100, 0.5)).toBe(MAX_ZOOM);
    expect(clampScale(0.001, 0.5)).toBeCloseTo(0.25);
  });
  it("зум сохраняет точку под курсором", () => {
    const v = { s: 1, x: 0, y: 0 };
    const z = zoomAt(v, 2, 100, 50, 1);
    // точка изображения под курсором (cx - x)/s не меняется: до (100-0)/1 = 100, после (100 - z.x)/2 = 100
    expect((100 - z.x) / z.s).toBeCloseTo(100);
    expect((50 - z.y) / z.s).toBeCloseTo(50);
  });
  it("колесо: вперёд приближает, назад отдаляет; строки и пиксели", () => {
    expect(wheelFactor(-100)).toBeGreaterThan(1);
    expect(wheelFactor(100)).toBeLessThan(1);
    expect(wheelFactor(-3, 1)).toBeCloseTo(wheelFactor(-48, 0));
  });
  it("изображение нельзя утащить целиком за край", () => {
    const v = clampPan({ s: 1, x: 5000, y: -5000 }, 800, 600, 1000, 1000, 80);
    expect(v.x).toBe((1000 + 800) / 2 - 80);
    expect(v.y).toBe(-((1000 + 600) / 2 - 80));
    expect(clampPan({ s: 0.1, x: 900, y: 0 }, 800, 600, 1000, 1000).x).toBeLessThan(900);
  });
  it("двойной щелчок: из «вписано» — приблизить, из приближенного — вернуть", () => {
    expect(doubleTapTarget(0.2, 0.2)).toBe(1);
    expect(doubleTapTarget(1, 0.2)).toBe(0.2);
    expect(doubleTapTarget(1, 1)).toBe(2);
  });
  it("листание не выходит за края", () => {
    expect(step(0, -1, 5)).toBe(0);
    expect(step(4, 1, 5)).toBe(4);
    expect(step(2, 1, 5)).toBe(3);
  });
});

describe("контекстное меню", () => {
  it("не выходит за края экрана", () => {
    expect(placeMenu(100, 100, 200, 150, 1000, 800)).toEqual({ left: 100, top: 100 });
    expect(placeMenu(950, 100, 200, 150, 1000, 800).left).toBe(750);
    expect(placeMenu(100, 780, 200, 150, 1000, 800).top).toBe(800 - 150 - 8);
    expect(placeMenu(2, 2, 200, 150, 1000, 800)).toEqual({ left: 8, top: 8 });
  });
  it("браузерное меню не перехватывается там, где оно нужнее", () => {
    expect(shouldKeepNative({ shift: true, targetTag: "DIV", selection: "" })).toBe(true);
    expect(shouldKeepNative({ shift: false, targetTag: "TEXTAREA", selection: "" })).toBe(true);
    expect(shouldKeepNative({ shift: false, targetTag: "P", selection: "выделено" })).toBe(true);
    expect(shouldKeepNative({ shift: false, targetTag: "P", selection: "  " })).toBe(false);
  });
});

describe("копирование всей транскрипции", () => {
  it("формат: время и имя, под ними реплика; без служебных полей", () => {
    const t = transcriptCopyText([{ started_at: "2026-10-09T10:14:03", display_name: "Иван Петров", text: "Реплика 1" }, { started_at: "2026-10-09T10:14:18", display_name: "Алексей Мороз", text: "Реплика 2" }]);
    expect(t).toBe("10:14:03 Иван Петров\nРеплика 1\n\n10:14:18 Алексей Мороз\nРеплика 2\n");
    expect(transcriptCopyText([])).toBe("");
  });
});
