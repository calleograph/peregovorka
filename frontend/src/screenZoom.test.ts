import { describe, expect, it } from "vitest";
import { IDENTITY, SCALE_CEIL, SCALE_FLOOR, clampView, contentRect, maxScale, panBy, percent, wheelFactor, zoomAt } from "./screenZoom";

const stage = { w: 1200, h: 700 };
const video = { w: 1920, h: 1080 };

describe("contentRect", () => {
  it("вписывает 16:9 в сцену 1200×700 с полями сверху и снизу", () => {
    const r = contentRect(stage, video, false);
    expect(r.w).toBeCloseTo(1200); expect(r.h).toBeCloseTo(675); expect(r.y).toBeCloseTo(12.5); expect(r.x).toBeCloseTo(0);
  });
  it("в режиме «заполнить» кадр больше сцены", () => {
    const r = contentRect(stage, video, true);
    expect(r.h).toBeCloseTo(700); expect(r.w).toBeGreaterThan(1200);
  });
  it("без размеров видео — вся сцена", () => {
    expect(contentRect(stage, { w: 0, h: 0 }, false)).toEqual({ x: 0, y: 0, w: 1200, h: 700 });
  });
});

describe("пределы приближения", () => {
  it("не меньше ×3 и не больше ×10", () => {
    expect(maxScale({ w: 1920, h: 1080 }, video, false)).toBe(SCALE_FLOOR);
    expect(maxScale({ w: 300, h: 200 }, { w: 3840, h: 2160 }, false)).toBe(SCALE_CEIL);
  });
  it("зависит от разрешения источника: 4K на сцене 1200 px — около ×6,4", () => {
    expect(maxScale(stage, { w: 3840, h: 2160 }, false)).toBeCloseTo(6.4, 1);
  });
});

describe("zoomAt", () => {
  it("точка под курсором остаётся на месте", () => {
    const v = zoomAt(IDENTITY, 2, 900, 300, stage, video, false);
    expect(v.s).toBeCloseTo(2);
    // координата изображения под курсором до и после совпадает: (c - x) / s
    expect((900 - v.x) / v.s).toBeCloseTo(900, 3);
    expect((300 - v.y) / v.s).toBeCloseTo(300, 3);
  });
  it("уменьшить меньше исходного размера нельзя, вид возвращается к вписанному", () => {
    let v = zoomAt(IDENTITY, 3, 600, 350, stage, video, false);
    v = zoomAt(v, 0.01, 600, 350, stage, video, false);
    expect(v).toEqual(IDENTITY);
  });
  it("приблизить бесконечно нельзя", () => {
    let v = IDENTITY;
    for (let i = 0; i < 200; i++) v = zoomAt(v, 1.5, 100, 100, stage, video, false);
    expect(v.s).toBeCloseTo(maxScale(stage, video, false));
  });
  it("после приближения к углу кадр не отрывается от краёв сцены", () => {
    const v = zoomAt(IDENTITY, 3, 0, 0, stage, video, false);
    const r = contentRect(stage, video, false);
    expect(v.x + r.x * v.s).toBeLessThanOrEqual(0.001);
    expect(v.x + (r.x + r.w) * v.s).toBeGreaterThanOrEqual(stage.w - 0.001);
    expect(v.y + (r.y + r.h) * v.s).toBeGreaterThanOrEqual(stage.h - 0.001);
  });
});

describe("panBy и clampView", () => {
  it("сдвиг ограничен краями изображения", () => {
    const z = zoomAt(IDENTITY, 2, 600, 350, stage, video, false);
    const far = panBy(z, 99999, 99999, stage, video, false);
    const r = contentRect(stage, video, false);
    expect(far.x + r.x * far.s).toBeCloseTo(0, 3);                         // левый край кадра у левого края сцены
    const farBack = panBy(z, -99999, -99999, stage, video, false);
    expect(farBack.x + (r.x + r.w) * farBack.s).toBeCloseTo(stage.w, 3);   // правый — у правого
  });
  it("при s = 1 сдвиг не накапливается", () => {
    expect(panBy(IDENTITY, 50, 50, stage, video, false)).toEqual(IDENTITY);
  });
  it("если по одной оси кадр целиком помещается — он по центру, по другой можно двигать", () => {
    const tall = { w: 600, h: 1400 };                       // вертикальный экран на широкой сцене
    const v = clampView({ s: 1.5, x: -500, y: -500 }, stage, tall, false);
    const r = contentRect(stage, tall, false);
    expect(r.w * v.s).toBeLessThan(stage.w);                // по ширине помещается
    expect(v.x + r.x * v.s).toBeCloseTo((stage.w - r.w * v.s) / 2, 3);
    expect(v.y).toBeLessThanOrEqual(0);
  });
  it("изменение размера сцены (полный экран) пересчитывает допустимый сдвиг", () => {
    const z = zoomAt(IDENTITY, 3, 1100, 650, stage, video, false);
    const big = clampView(z, { w: 1920, h: 1080 }, video, false);
    expect(big.s).toBeLessThanOrEqual(maxScale({ w: 1920, h: 1080 }, video, false));
    expect(big.x).toBeGreaterThanOrEqual(1920 - 1920 * big.s - 0.001);
  });
});

describe("wheelFactor", () => {
  it("вперёд приближает, назад отдаляет, шаги взаимно обратны", () => {
    expect(wheelFactor(-100)).toBeGreaterThan(1);
    expect(wheelFactor(100)).toBeLessThan(1);
    expect(wheelFactor(-100) * wheelFactor(100)).toBeCloseTo(1, 10);
  });
  it("один шаг ограничен — резкий скролл не «выстреливает»", () => {
    expect(wheelFactor(-100000)).toBeCloseTo(wheelFactor(-300));
    expect(wheelFactor(-100000)).toBeLessThan(2);
  });
  it("строки и страницы пересчитываются в пиксели, ctrl (щипок) чувствительнее", () => {
    expect(wheelFactor(-3, 1)).toBeCloseTo(wheelFactor(-48));
    expect(wheelFactor(-5, 0, true)).toBeGreaterThan(wheelFactor(-5, 0, false));
  });
});

it("percent: 100% — вписанный вид", () => {
  expect(percent(IDENTITY)).toBe("100%");
  expect(percent({ s: 2.5, x: 0, y: 0 })).toBe("250%");
});
