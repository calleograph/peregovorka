import { describe, expect, it } from "vitest";
import { NET_LINKS, NET_NODES, linkPath, wantsMotion, wantsNetwork } from "./glassMath";

describe("фон входа «встреча на связи»", () => {
  it("узлы не заходят в середину окна, где форма входа", () => {
    for (const n of NET_NODES) {
      const left = n.x + n.w <= 520, right = n.x >= 680;
      expect(left || right, n.id).toBe(true);
      expect(n.x).toBeGreaterThanOrEqual(0); expect(n.x + n.w).toBeLessThanOrEqual(1200); expect(n.y + n.h).toBeLessThanOrEqual(800);
    }
  });
  it("все связи ведут к существующим узлам, кривая — корректный путь", () => {
    const ids = new Set(NET_NODES.map((n) => n.id));
    for (const [a, b] of NET_LINKS) { expect(ids.has(a) && ids.has(b)).toBe(true); expect(linkPath(NET_NODES.find((n) => n.id === a)!, NET_NODES.find((n) => n.id === b)!)).toMatch(/^M[\d.]+ [\d.]+ Q-?\d+ -?\d+ [\d.]+ [\d.]+$/); }
  });
  it("движение выключено при «уменьшить движение», экономии трафика и слабом устройстве", () => {
    expect(wantsMotion({ reduced: false, cores: 8 })).toBe(true);
    expect(wantsMotion({ reduced: true, cores: 8 })).toBe(false);
    expect(wantsMotion({ reduced: false, cores: 8, saveData: true })).toBe(false);
    expect(wantsMotion({ reduced: false, cores: 2 })).toBe(false);
  });
  it("на узком телефоне композиции нет", () => { expect(wantsNetwork(390)).toBe(false); expect(wantsNetwork(1366)).toBe(true); });
});
