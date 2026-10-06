import { describe, expect, it } from "vitest";
import { buildRoomOptions } from "./roomOptions";
import { screenShareOptions } from "./screenShare";

describe("настройки Room: камера и показ экрана различаются осознанно", () => {
  const o = buildRoomOptions();
  const pd = o.publishDefaults!;

  it("adaptiveStream, dynacast и simulcast включены", () => {
    expect(o.adaptiveStream).toBe(true);
    expect(o.dynacast).toBe(true);
    expect(pd.simulcast).toBe(true);
    expect(pd.videoSimulcastLayers?.length).toBeGreaterThanOrEqual(2);
  });

  it("камера: умеренный битрейт и до 24 к/с; экран: не больше 15 к/с по умолчанию", () => {
    expect(pd.videoEncoding?.maxFramerate).toBeLessThanOrEqual(30);
    expect(pd.screenShareEncoding?.maxFramerate).toBeLessThanOrEqual(15);
    expect(pd.screenShareEncoding?.maxBitrate).not.toBe(pd.videoEncoding?.maxBitrate);
    expect(pd.videoCodec).toBe("vp8");
  });

  it("профиль «чёткость» (по умолчанию) держит разрешение и 10–15 к/с — читаемость текста важнее плавности", () => {
    const s = screenShareOptions("sharp", false);
    expect(s.publish.degradationPreference).toBe("maintain-resolution");
    expect(s.publish.screenShareEncoding?.maxFramerate).toBeGreaterThanOrEqual(10);
    expect(s.publish.screenShareEncoding?.maxFramerate).toBeLessThanOrEqual(15);
    expect(s.capture.contentHint).toBe("text");
  });

  it("плавный профиль для видео — единственный, где FPS приоритетнее разрешения", () => {
    expect(screenShareOptions("motion", false).publish.degradationPreference).toBe("maintain-framerate");
    expect(screenShareOptions("balanced", false).publish.degradationPreference).toBe("balanced");
  });
});
