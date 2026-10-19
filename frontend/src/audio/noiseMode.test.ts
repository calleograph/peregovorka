import { describe, expect, it, vi } from "vitest";
import type { LocalAudioTrack } from "livekit-client";
import { applyNoiseMode, browserNoiseSuppression, isNoiseMode, migrateNoiseMode } from "./noiseMode";

const fakeTrack = (setProcessor: () => Promise<void>) => ({
  getProcessor: vi.fn(() => undefined), stopProcessor: vi.fn(async () => undefined), setProcessor: vi.fn(setProcessor),
}) as unknown as LocalAudioTrack;

describe("режимы шумоподавления", () => {
  it("распознаёт режимы и переносит старый флаг", () => {
    expect(["off", "standard", "enhanced"].every(isNoiseMode)).toBe(true);
    expect(isNoiseMode("loud")).toBe(false);
    expect(migrateNoiseMode({ noiseSuppression: false })).toBe("off");
    expect(migrateNoiseMode({})).toBe("enhanced");            // выбора нет — режим по умолчанию
    expect(migrateNoiseMode({ noiseSuppression: true })).toBe("standard");   // прежний явный выбор не меняется
    expect(migrateNoiseMode({ noiseMode: "enhanced", noiseSuppression: false })).toBe("enhanced");
  });

  it("браузерное подавление включено только в стандартном режиме", () => {
    expect([browserNoiseSuppression("off"), browserNoiseSuppression("standard"), browserNoiseSuppression("enhanced")]).toEqual([false, true, false]);
  });

  it("стандартный и выключенный режимы только перезапускают захват с нужным флагом", async () => {
    const restart = vi.fn(async () => undefined);
    const t = fakeTrack(async () => undefined);
    expect(await applyNoiseMode(t, "standard", restart, () => undefined)).toEqual({ mode: "standard", fellBack: false });
    expect(await applyNoiseMode(t, "off", restart, () => undefined)).toEqual({ mode: "off", fellBack: false });
    expect(restart.mock.calls).toEqual([[true], [false]]);
    expect(t.setProcessor).not.toHaveBeenCalled();
  });

  it("усиленный режим без AudioWorklet (среда без браузерных API) откатывается на стандартный", async () => {
    const restart = vi.fn(async () => undefined);
    const t = fakeTrack(async () => undefined);
    const r = await applyNoiseMode(t, "enhanced", restart, () => undefined);
    expect(r.fellBack).toBe(true);
    expect(r.mode).toBe("standard");
    expect(restart).toHaveBeenLastCalledWith(true);          // после отката браузерное подавление снова включено
    expect(t.setProcessor).not.toHaveBeenCalled();
  });
});
