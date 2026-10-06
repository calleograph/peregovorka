import { describe, expect, it } from "vitest";
import { screenShareOptions } from "./screenShare";

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

  it("always offers an extra low simulcast layer and VP9", () => {
    for (const p of ["sharp", "balanced", "motion"] as const) {
      const o = screenShareOptions(p, false);
      expect(o.publish.videoCodec).toBe("vp9");
      expect(o.publish.screenShareSimulcastLayers?.length).toBe(1);
    }
  });
});
