import { describe, expect, it } from "vitest";
import { levelFromTimeDomain, setPreJoin, takePreJoin, toneWav } from "./prejoin";

describe("prejoin", () => {
  it("тон — корректный WAV нужной длительности, без щелчков на краях", () => {
    const w = toneWav(440, 0.5, 8000);
    const s = new TextDecoder().decode(w.slice(0, 4)) + new TextDecoder().decode(w.slice(8, 12));
    expect(s).toBe("RIFFWAVE");
    const v = new DataView(w.buffer);
    expect(v.getUint32(24, true)).toBe(8000);
    expect(v.getUint32(40, true)).toBe(8000 * 0.5 * 2);
    expect(v.getInt16(44, true)).toBe(0);
    expect(Math.abs(v.getInt16(w.length - 2, true))).toBeLessThan(400);
    let peak = 0; for (let i = 44; i < w.length; i += 2) peak = Math.max(peak, Math.abs(v.getInt16(i, true)));
    expect(peak).toBeGreaterThan(5000);
  });

  it("уровень: тишина — 0, громкий сигнал — близко к 1, значения в пределах 0…1", () => {
    expect(levelFromTimeDomain(new Uint8Array(256).fill(128))).toBe(0);
    const loud = new Uint8Array(256).map((_, i) => (i % 2 ? 255 : 0));
    expect(levelFromTimeDomain(loud)).toBe(1);
    const quiet = new Uint8Array(256).map((_, i) => 128 + (i % 2 ? 6 : -6));
    const q = levelFromTimeDomain(quiet);
    expect(q).toBeGreaterThan(0); expect(q).toBeLessThan(0.3);
    expect(levelFromTimeDomain(new Uint8Array(0))).toBe(0);
  });

  it("выбор устройств забирается один раз", () => {
    setPreJoin({ micId: "m1", camOn: false });
    expect(takePreJoin()).toEqual({ micId: "m1", camOn: false });
    expect(takePreJoin()).toBeNull();
  });
});
