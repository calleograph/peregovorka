import { describe, expect, it } from "vitest";
import { backoffDelay } from "./liveSocket";

describe("пауза переподключения", () => {
  it("нарастает экспоненциально и ограничена 15 секундами", () => {
    const mid = () => 0.5; // без случайности: множитель ровно 1.0
    expect([0, 1, 2, 3, 4, 5, 6, 10].map((n) => backoffDelay(n, mid))).toEqual([500, 1000, 2000, 4000, 8000, 15000, 15000, 15000]);
  });

  it("случайность в пределах ±30 %", () => {
    expect(backoffDelay(3, () => 0)).toBe(2800);
    expect(backoffDelay(3, () => 1)).toBe(5200);
  });
});
