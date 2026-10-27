import { describe, expect, it } from "vitest";
import { follow, rng, shardCount, shardLayout, wantsWebGL } from "./glassMath";

describe("glassMath", () => {
  it("раскладка детерминирована и не занимает середину", () => {
    const a = shardLayout(8, 5, 10), b = shardLayout(8, 5, 10);
    expect(a).toEqual(b);
    expect(a.every((s) => Math.abs(s.x) >= 8 * 0.46 - 1e-9)).toBe(true);
    expect(a.every((s) => Math.abs(s.y) <= 5)).toBe(true);
    expect(new Set(a.map((s) => Math.sign(s.x))).size).toBe(2);               // есть кристаллы и слева, и справа
  });
  it("генератор даёт значения в [0,1)", () => { const r = rng(1); for (let i = 0; i < 100; i++) { const v = r(); expect(v).toBeGreaterThanOrEqual(0); expect(v).toBeLessThan(1); } });
  it("follow приближается к цели и не перелетает", () => {
    let v = 0;
    for (let i = 0; i < 60; i++) { const n = follow(v, 1, 1 / 30); expect(n).toBeGreaterThanOrEqual(v); expect(n).toBeLessThanOrEqual(1); v = n; }
    expect(v).toBeGreaterThan(0.9);
    expect(follow(0.3, 1, 0)).toBe(0.3);
  });
  it("WebGL не включается при «уменьшить движение», экономии трафика, слабом устройстве и узком экране", () => {
    expect(wantsWebGL({ reduced: false, cores: 8, width: 1280 })).toBe(true);
    expect(wantsWebGL({ reduced: true, cores: 8, width: 1280 })).toBe(false);
    expect(wantsWebGL({ reduced: false, cores: 8, saveData: true, width: 1280 })).toBe(false);
    expect(wantsWebGL({ reduced: false, cores: 2, width: 1280 })).toBe(false);
    expect(wantsWebGL({ reduced: false, cores: 8, width: 390 })).toBe(false);
  });
  it("кристаллов тем больше, чем шире окно", () => { expect(shardCount(390)).toBeLessThan(shardCount(1280)); expect(shardCount(1280)).toBeLessThan(shardCount(1920)); });
});
