import { describe, expect, it } from "vitest";
import { summarizeMicProbe, type MicProbeReport } from "./micProbe";

const base: MicProbeReport = {
  at: "2026-01-01T00:00:00Z", totalMs: 1200,
  env: { chromium: "148.0.7778.96", brands: "", platform: "Windows", desktopApp: "1.4.0", secure: true, permission: "granted" },
  devices: [{ kind: "audioinput", label: "Default - Mic", role: "default", group: "a" }, { kind: "audioinput", label: "Communications - Mic", role: "communications", group: "a" }, { kind: "audiooutput", label: "Spk", role: "device", group: "b" }],
  opens: [{ ok: true, ms: 120, peak: 0.2, settings: { sampleRate: 48000, channels: 1, latency: 0.01 } }, { ok: true, ms: 80 }, { ok: false, ms: 40, error: "NotReadableError: Could not start audio source" }],
  concurrent: { first: { ok: true, ms: 50 }, second: { ok: false, ms: 30, error: "NotReadableError: busy" } },
  audioContext: { sinkNone: { supported: true, ms: 20, state: "running" }, sinkDefault: { ms: 60, state: "running" } },
};

describe("сводка проверки микрофона", () => {
  it("показывает версию движка, число открытий, время и ошибку", () => {
    const t = summarizeMicProbe(base).join("\n");
    expect(t).toContain("Chromium 148.0.7778.96 · приложение Windows 1.4.0");
    expect(t).toContain("Открытий микрофона: 2 из 3, время 80–120 мс · ошибка: NotReadableError");
    expect(t).toContain("не открылся (NotReadableError: busy)");
    expect(t).toContain("«Связь»: есть");
  });
  it("отличает отсутствие сигнала в потоке", () => {
    const quiet = { ...base, opens: [{ ...base.opens[0], peak: 0 }] };
    expect(summarizeMicProbe(quiet).join("\n")).toContain("сигнала не было");
  });
});
