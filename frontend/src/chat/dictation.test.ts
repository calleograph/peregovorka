import { afterEach, describe, expect, it, vi } from "vitest";
import { Dictation } from "./dictation";

type Track = { readyState: string; getSettings: () => { deviceId?: string } };
const withMic = (track: Track | null, devices: { kind: string; deviceId: string }[] | "fail") => {
  vi.stubGlobal("navigator", { mediaDevices: { enumerateDevices: () => (devices === "fail" ? Promise.reject(new Error("x")) : Promise.resolve(devices)) } });
  const d = new Dictation({ onFinal: () => undefined, onPhase: () => undefined });
  (d as unknown as { stream: unknown }).stream = track ? { getAudioTracks: () => [track] } : null;
  return d;
};
const live = (id?: string): Track => ({ readyState: "live", getSettings: () => ({ deviceId: id }) });

describe("диктовка: потеря микрофона", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("событие смены устройств при живом микрофоне не останавливает диктовку (в Windows оно приходит и при открытии микрофона)", async () => {
    expect(await withMic(live("mic1"), [{ kind: "audioinput", deviceId: "mic1" }, { kind: "audioinput", deviceId: "mic2" }]).micLost()).toBe(false);
  });
  it("микрофон считается потерянным, если дорожка закончилась или устройства нет в списке", async () => {
    expect(await withMic({ readyState: "ended", getSettings: () => ({}) }, []).micLost()).toBe(true);
    expect(await withMic(live("mic1"), [{ kind: "audioinput", deviceId: "mic2" }]).micLost()).toBe(true);
    expect(await withMic(null, []).micLost()).toBe(true);
  });
  it("нельзя проверить список устройств или у дорожки нет идентификатора — диктовку не обрываем", async () => {
    expect(await withMic(live("mic1"), "fail").micLost()).toBe(false);
    expect(await withMic(live(undefined), []).micLost()).toBe(false);
  });
});
