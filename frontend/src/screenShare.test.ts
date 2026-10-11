import { describe, expect, it } from "vitest";
import { ECO, applyProfileLive, clampEcoKbps, ecoReport, ecoScale, isScreenProfile, loadProfile, saveProfile, screenShareOptions } from "./screenShare";

describe("screen share profiles", () => {
  it("sharp keeps resolution for text and hints detail", () => {
    const o = screenShareOptions("sharp", false);
    expect(o.capture.contentHint).toBe("text");
    expect(o.capture.resolution?.height).toBe(1080);
    expect(o.publish.degradationPreference).toBe("maintain-resolution");
    expect(o.publish.screenShareEncoding?.maxFramerate).toBe(15);
    expect(o.capture.audio).toBe(false);
  });

  it("motion prefers frame rate (30 fps) over resolution", () => {
    const o = screenShareOptions("motion", true);
    expect(o.capture.contentHint).toBe("motion");
    expect(o.publish.degradationPreference).toBe("maintain-framerate");
    expect(o.publish.screenShareEncoding?.maxFramerate).toBe(30);
    expect(o.capture.audio).toBe(true);
    expect(o.capture.systemAudio).toBe("include");
  });

  it("the three classic profiles still offer an extra low simulcast layer and VP9 (unchanged by the eco profile)", () => {
    for (const p of ["sharp", "balanced", "motion"] as const) {
      const o = screenShareOptions(p, false);
      expect(o.publish.videoCodec).toBe("vp9");
      expect(o.publish.screenShareSimulcastLayers?.length).toBe(1);
    }
  });

  it("eco: 1280x720, 10 fps, 800 kbps, one layer, no simulcast, resolution is kept for readability", () => {
    const o = screenShareOptions("eco", false);
    expect(o.capture.resolution).toEqual({ width: 1280, height: 720, frameRate: 10 });
    expect(o.publish.screenShareEncoding).toEqual({ maxBitrate: 800_000, maxFramerate: 10 });
    expect(o.publish.simulcast).toBe(false);
    expect(o.publish.screenShareSimulcastLayers).toEqual([]);
    expect(o.publish.degradationPreference).toBe("maintain-resolution");
    expect(o.capture.contentHint).toBe("text");
  });

  it("eco bitrate stays within 500-900 kbps whatever the setting says", () => {
    expect(clampEcoKbps(100)).toBe(500);
    expect(clampEcoKbps(5000)).toBe(900);
    expect(clampEcoKbps("abc")).toBe(800);
    expect(clampEcoKbps(650.4)).toBe(650);
    expect(screenShareOptions("eco", false, 900).publish.screenShareEncoding?.maxBitrate).toBe(900_000);
    expect(screenShareOptions("eco", false, 10).publish.screenShareEncoding?.maxBitrate).toBe(500_000);
  });

  it("recognises profiles", () => {
    expect(["sharp", "balanced", "motion", "eco"].every(isScreenProfile)).toBe(true);
    expect(isScreenProfile("ultra")).toBe(false);
    expect(isScreenProfile(null)).toBe(false);
  });
});

describe("saved choice", () => {
  const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } }; };
  it("falls back to the server default, then remembers the choice of the participant", () => {
    const st = mem();
    expect(loadProfile("balanced", st)).toBe("balanced");
    expect(loadProfile("nonsense", st)).toBe("sharp");
    saveProfile("eco", st);
    expect(loadProfile("balanced", st)).toBe("eco");
  });
  it("survives unavailable storage", () => {
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(loadProfile("motion", broken)).toBe("motion");
    expect(() => saveProfile("eco", broken)).not.toThrow();
  });
});

describe("eco honesty about what the browser really sends", () => {
  it("scale only shrinks, keeps aspect", () => {
    expect(ecoScale(1920, 1080)).toBeCloseTo(1.5, 5);
    expect(ecoScale(2560, 1440)).toBeCloseTo(2, 5);
    expect(ecoScale(1280, 720)).toBe(1);
    expect(ecoScale(800, 600)).toBe(1);
    expect(ecoScale(undefined, undefined)).toBe(1);
  });

  it("reports success only when measured values fit", () => {
    const good = ecoReport({ frameWidth: 1280, frameHeight: 720, framesPerSecond: 10, targetBitrate: 790_000 });
    expect(good.ok).toBe(true);
    expect(good.text).toContain("1280×720");
    const bad = ecoReport({ frameWidth: 1920, frameHeight: 1080, framesPerSecond: 15, targetBitrate: 2_000_000 });
    expect(bad.ok).toBe(false);
    expect(bad.text).toContain("не уложился");
    expect(ecoReport(undefined).ok).toBe(false);
  });

  it("applies live limits on the sender without stopping the track", async () => {
    const applied: Record<string, unknown>[] = [];
    const params = { encodings: [{ maxBitrate: 4_000_000 }, { maxBitrate: 500_000 }] as Record<string, unknown>[] };
    const track = {
      mediaStreamTrack: { contentHint: "text", getSettings: () => ({ width: 1920, height: 1080 }), applyConstraints: async (c: unknown) => { applied.push(c as Record<string, unknown>); } },
      sender: { getParameters: () => params, setParameters: async () => undefined },
      getSenderStats: async () => [],
    };
    const r = await applyProfileLive(track as never, "eco");
    expect(r).toBe("partial");                       // два слоя остались от прежнего профиля: кодек и слои на лету не меняются — честно сообщаем
    expect(params.encodings[0]).toMatchObject({ maxBitrate: ECO.kbps * 1000, maxFramerate: 10 });
    expect(params.encodings[0].scaleResolutionDownBy).toBeCloseTo(1.5, 5);
    expect(applied[0]).toEqual({ width: { max: 1280 }, height: { max: 720 }, frameRate: { max: 10 } });
    expect(await applyProfileLive(undefined, "eco")).toBe("none");
  });
});
