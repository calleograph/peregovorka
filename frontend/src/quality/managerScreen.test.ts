import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Room } from "livekit-client";
import { QualityManager } from "./manager";

/** Поддельный отправитель показа экрана: запоминает, что в него записали, и отдаёт замеры, которые задаёт сценарий. */
function rig() {
  const enc = { maxBitrate: 4_000_000, maxFramerate: 15, scaleResolutionDownBy: 1 } as RTCRtpEncodingParameters;
  const writes: { maxBitrate?: number; maxFramerate?: number; scale?: number }[] = [];
  let stats = { packetsSent: 0, packetsLost: 0, qualityLimitationReason: "none", roundTripTime: 0.04 };
  const track = {
    mediaStreamTrack: { id: "scr1", getSettings: () => ({ width: 1920, height: 1080 }) },
    sender: { getParameters: () => ({ encodings: [enc] }), setParameters: async (p: { encodings: RTCRtpEncodingParameters[] }) => { writes.push({ maxBitrate: p.encodings[0].maxBitrate, maxFramerate: p.encodings[0].maxFramerate, scale: p.encodings[0].scaleResolutionDownBy }); } },
    getSenderStats: async () => [stats],
  };
  let conn = "excellent";
  const room = { localParticipant: { get connectionQuality() { return conn; }, isCameraEnabled: false, getTrackPublication: (src: string) => (src === "screen_share" ? { videoTrack: track } : undefined) } } as unknown as Room;
  const notes: string[] = [];
  const mgr = new QualityManager(room, () => undefined, (t) => notes.push(t));
  const tick = () => (mgr as unknown as { tick: () => Promise<void> }).tick();
  return {
    mgr, notes, writes, tick,
    bad: () => { conn = "poor"; stats = { packetsSent: stats.packetsSent + 500, packetsLost: stats.packetsLost + 100, qualityLimitationReason: "bandwidth", roundTripTime: 0.3 }; },
    good: () => { conn = "excellent"; stats = { packetsSent: stats.packetsSent + 500, packetsLost: stats.packetsLost, qualityLimitationReason: "none", roundTripTime: 0.04 }; },
  };
}

describe("менеджер качества: показ экрана выступающего", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(0); });
  afterEach(() => vi.useRealTimers());

  const steps = async (r: ReturnType<typeof rig>, n: number, kind: "bad" | "good") => { for (let i = 0; i < n; i++) { vi.advanceTimersByTime(5000); r[kind](); await r.tick(); } };

  it("канал не тянет: потолки битрейта, частоты и разрешения снижаются по ступеням, выступающему приходит спокойное сообщение", async () => {
    const r = rig();
    await r.tick();                                      // первый замер даёт отсчёт потерь
    await steps(r, 6, "bad");
    expect(r.writes.length).toBeGreaterThanOrEqual(3);
    expect(r.writes[0].maxBitrate).toBe(2_600_000);      // 4 Мбит/с × 0,65
    expect(r.writes.at(-1)?.maxBitrate).toBeLessThan(r.writes[0].maxBitrate!);
    expect(r.writes.at(-1)?.scale).toBe(3);
    expect(r.notes[0]).toMatch(/качество показа снижено \(1444×812, 12 к\/с, до 2600 кбит\/с\)/);
    expect(r.notes[0]).toMatch(/Вернём качество само/);
  });

  it("связь выровнялась и держится: качество возвращается ступенями до исходного, на каждой ступени есть сообщение", async () => {
    const r = rig();
    await r.tick();
    await steps(r, 6, "bad");
    const downs = r.notes.length;
    await steps(r, 60, "good");
    const last = r.writes.at(-1);
    expect(last).toMatchObject({ maxBitrate: 4_000_000, maxFramerate: 15, scale: 1 });
    expect(r.notes.length).toBeGreaterThan(downs);
    expect(r.notes.at(-1)).toBe("Связь стабильна: показ экрана снова в полном качестве.");
    expect(r.mgr.getScreenLevel()).toBe(0);
  });

  it("автоподстройку можно выключить: параметры возвращаются как у выбранного профиля и больше не меняются", async () => {
    const r = rig();
    await r.tick();
    await steps(r, 4, "bad");
    expect(r.mgr.getScreenLevel()).toBeGreaterThan(0);
    await r.mgr.setScreenAuto(false);
    expect(r.writes.at(-1)).toMatchObject({ maxBitrate: 4_000_000, scale: 1 });
    const n = r.writes.length;
    await steps(r, 10, "bad");
    expect(r.writes.length).toBe(n);
  });

  it("хороший канал: ничего не трогаем", async () => {
    const r = rig();
    await r.tick();
    await steps(r, 60, "good");
    expect(r.writes).toEqual([]);
    expect(r.notes).toEqual([]);
  });
});
