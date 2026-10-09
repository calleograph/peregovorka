import { describe, expect, it } from "vitest";
import { drift, energyAt, gridSpec, pointerPush, qualityFor } from "./backdropMath";

describe("фон страницы входа", () => {
  it("сетка покрывает окно с запасом и центрирована", () => {
    const g = gridSpec(1000, 600, 50);
    expect(g.cols).toBe(22); expect(g.rows).toBe(14);
    expect(g.ox + (g.cols - 1) * g.step).toBeGreaterThan(1000);
    expect(g.ox).toBeLessThan(0);
  });
  it("курсор отталкивает близкие узлы, дальние (за радиусом) не трогает, в центре силы нет (нет деления на ноль)", () => {
    expect(pointerPush(100, 100, 100, 100, 150, 20)).toEqual([0, 0]);
    expect(pointerPush(400, 100, 100, 100, 150, 20)).toEqual([0, 0]);
    const [dx, dy] = pointerPush(150, 100, 100, 100, 150, 20);
    expect(dx).toBeGreaterThan(0); expect(Math.abs(dy)).toBeLessThan(1e-9);
    const near = Math.hypot(...pointerPush(120, 100, 100, 100, 150, 20)), far = Math.hypot(...pointerPush(220, 100, 100, 100, 150, 20));
    expect(near).toBeGreaterThan(far);
    expect(near).toBeLessThanOrEqual(20);
  });
  it("без активности «дыхание» затухает до нуля; энергия убывает линейно", () => {
    const [z1, z2] = drift(1, 5000, 4, 0); expect(Math.abs(z1) + Math.abs(z2)).toBe(0);
    const [x, y] = drift(1, 5000, 4, 1);
    expect(Math.abs(x)).toBeLessThanOrEqual(4); expect(Math.abs(y)).toBeLessThanOrEqual(4);
    expect(energyAt(0, 4000)).toBe(1); expect(energyAt(2000, 4000)).toBeCloseTo(0.5); expect(energyAt(9000, 4000)).toBe(0);
  });
  it("слабое устройство получает более редкую сетку и меньше кадров", () => {
    expect(qualityFor(2, 1920)).toEqual({ step: 84, fps: 20 });
    expect(qualityFor(8, 1920)).toEqual({ step: 56, fps: 30 });
    expect(qualityFor(8, 400).step).toBe(64);
    expect(qualityFor(undefined, 1920).fps).toBe(30);
  });
});
