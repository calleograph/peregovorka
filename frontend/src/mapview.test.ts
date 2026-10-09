import { beforeAll, describe, expect, it } from "vitest";
import viewerSource from "../public/mapview/viewer.js?raw";

// Файл просмотра — обычный скрипт для браузера; в тесте он выполняется без document и отдаёт только чистые функции.
type Util = {
  squarify: (items: { id: string; value: number }[], w: number, h: number) => { id: string; x: number; y: number; w: number; h: number }[];
  timelineBars: (topics: { id: string; segments: { start_s: number; end_s: number }[] }[], total: number) => { topic: string; left: number; width: number }[];
  ticks: (total: number) => number[]; duration: (s: number) => string; clockOffset: (s: number) => string; plural: (n: number, a: string, b: string, c: string) => string;
};
let U: Util;
beforeAll(() => {
  new Function(viewerSource)();      // eslint-disable-line no-new-func -- тест: запускаем собственный файл просмотра
  U = (globalThis as unknown as { PgMapUtil: Util }).PgMapUtil;
});

describe("карта разговора: геометрия", () => {
  it("блоки занимают всю площадь пропорционально времени и не пересекаются", () => {
    const items = [{ id: "a", value: 22 }, { id: "b", value: 10 }, { id: "c", value: 5 }, { id: "d", value: 3 }, { id: "e", value: 1 }];
    const r = U.squarify(items, 800, 340);
    expect(r).toHaveLength(5);
    const total = items.reduce((s, i) => s + i.value, 0);
    for (const i of items) {
      const c = r.find((x) => x.id === i.id)!;
      expect(c.w * c.h / (800 * 340)).toBeCloseTo(i.value / total, 6);
    }
    expect(r.reduce((s, c) => s + c.w * c.h, 0)).toBeCloseTo(800 * 340, 3);
    for (const c of r) {
      expect(c.x).toBeGreaterThanOrEqual(-1e-6);
      expect(c.y).toBeGreaterThanOrEqual(-1e-6);
      expect(c.x + c.w).toBeLessThanOrEqual(800 + 1e-6);
      expect(c.y + c.h).toBeLessThanOrEqual(340 + 1e-6);
    }
    for (let i = 0; i < r.length; i++) {
      for (let j = i + 1; j < r.length; j++) {
        const a = r[i], b = r[j];
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x), oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(ox > 1e-6 && oy > 1e-6).toBe(false);
      }
    }
  });
  it("пустые и нулевые темы не ломают раскладку", () => {
    expect(U.squarify([], 100, 100)).toEqual([]);
    expect(U.squarify([{ id: "x", value: 0 }], 100, 100)).toEqual([]);
    expect(U.squarify([{ id: "x", value: 5 }], 100, 50)).toEqual([{ id: "x", x: 0, y: 0, w: 100, h: 50 }]);
  });
  it("шкала: ширина отрезка пропорциональна времени, короткий отрезок остаётся видимым", () => {
    const bars = U.timelineBars([{ id: "t", segments: [{ start_s: 0, end_s: 300 }, { start_s: 2820, end_s: 2825 }] }], 3600);
    expect(bars[0].left).toBe(0);
    expect(bars[0].width).toBeCloseTo(300 / 3600 * 100, 6);
    expect(bars[1].left).toBeCloseTo(2820 / 3600 * 100, 6);
    expect(bars[1].width).toBeGreaterThanOrEqual(0.4);
    expect(U.timelineBars([], 0)).toEqual([]);
  });
  it("деления шкалы и подписи", () => {
    expect(U.ticks(1800)).toEqual([0, 300, 600, 900, 1200, 1500, 1800]);
    expect(U.ticks(5400).length).toBeLessThanOrEqual(9);
    expect(U.duration(22 * 60)).toBe("22 мин");
    expect(U.duration(75 * 60)).toBe("1 ч 15 мин");
    expect(U.duration(30)).toBe("30 с");
    expect(U.clockOffset(34 * 60 + 18)).toBe("00:34:18");
    expect(U.plural(2, "участник", "участника", "участников")).toBe("участника");
  });
});
