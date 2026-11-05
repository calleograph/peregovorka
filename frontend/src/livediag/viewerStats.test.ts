import { describe, expect, it, vi } from "vitest";
import { clientVerdict, ViewerStats, type ClientStats } from "./viewerStats";
import { bps, level } from "./gauges";

const base: ClientStats = { stalls: 0, stall_ms: 0, errs: 0, reconn: 0 };

describe("вывод для зрителя", () => {
  it("нормальный просмотр", () => { expect(clientVerdict({ ...base, buf: 2, seg_ms: 200, kbps: 5000, est: 50000 }, 0).level).toBe(0); });
  it("новая остановка с медленной загрузкой — канал", () => {
    const v = clientVerdict({ ...base, stalls: 2, buf: 0.1, seg_ms: 1400 }, 1);
    expect(v.level).toBe(2); expect(v.text).toContain("канал");
  });
  it("старые остановки не тревожат", () => { expect(clientVerdict({ ...base, stalls: 5, buf: 2, seg_ms: 200 }, 5).level).toBe(0); });
  it("канал на пределе без остановок — предупреждение", () => { expect(clientVerdict({ ...base, buf: 2, seg_ms: 1300 }, 0).level).toBe(1); });
  it("нет показателей браузера (Safari) — не считается проблемой", () => { expect(clientVerdict({ ...base, buf: 2 }, 0).level).toBe(0); });
});

describe("показатели зрителя", () => {
  it("событие загрузки сегмента hls.js даёт время, размер и поток", () => {
    const on: Record<string, (e: string, d: never) => void> = {};
    const s = new ViewerStats();
    s.attachHls({ on: (e, cb) => { on[e] = cb; }, bandwidthEstimate: 40_000_000 }, { FRAG_LOADED: "loaded", ERROR: "err" });
    on.loaded("loaded", { frag: { duration: 1.5, type: "main", stats: { loaded: 937_500, loading: { start: 0, end: 300 } } } } as never);
    on.err("err", { fatal: false } as never); on.err("err", { fatal: true } as never);
    const t = s.tick();
    expect(t.seg_ms).toBe(300); expect(t.kbps).toBe(5000); expect(t.est).toBe(40000); expect(t.errs).toBe(1); expect(t.eng).toBe("hlsjs");
  });
  it("история не растёт бесконечно", () => {
    const s = new ViewerStats();
    let now = 1_000_000;
    const clock = vi.spyOn(Date, "now").mockImplementation(() => now);
    for (let i = 0; i < 400; i++) { now += 2000; s.tick(); }
    clock.mockRestore();
    expect(s.history.length).toBe(150);
  });
});

describe("оформление значений", () => {
  it("нет значения — прочерк и «нет данных», а не ноль", () => { expect(bps(null)).toBe("—"); expect(level(null, 85, 95)).toBe(-1); });
  it("пороги", () => { expect(level(84, 85, 95)).toBe(0); expect(level(85, 85, 95)).toBe(1); expect(level(95, 85, 95)).toBe(2); });
});
