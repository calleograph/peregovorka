import { describe, expect, it } from "vitest";
import { NO_DATA, asrQueueText, barTone, gib, mbps, num, pct, tracksText } from "./performanceMath";

describe("performance formatting", () => {
  it("shows No data for anything missing", () => {
    expect(num(null)).toBe(NO_DATA);
    expect(num(undefined)).toBe(NO_DATA);
    expect(num(Number.NaN)).toBe(NO_DATA);
    expect(pct(null)).toBe(NO_DATA);
    expect(mbps(null)).toBe(NO_DATA);
    expect(gib(undefined)).toBe(NO_DATA);
    expect(tracksText(null, null)).toBe(NO_DATA);
    expect(asrQueueText(null)).toBe(NO_DATA);
  });
  it("formats values with units", () => {
    expect(pct(42.4)).toMatch(/^42\s?%$/);
    expect(mbps(800)).toBe("800 кбит/с");
    expect(mbps(2500)).toMatch(/^2,5\s?Мбит\/с$/);
    expect(gib(16 * 1073741824)).toMatch(/^16\s?ГБ$/);
    expect(tracksText(12, 3)).toBe("12 аудио, 3 видео");
    expect(tracksText(5, null)).toBe("5 аудио, 0 видео");
  });
  it("tone follows the fill level", () => {
    expect(barTone(10)).toBe("");
    expect(barTone(75)).toBe("warn");
    expect(barTone(95)).toBe("bad");
    expect(barTone(null)).toBe("");
  });
  it("tells when speech recognition lags", () => {
    expect(asrQueueText(0)).toBe("очередь пуста");
    expect(asrQueueText(3)).toBe("3 в очереди");
    expect(asrQueueText(12)).toContain("запаздывает");
  });
});
