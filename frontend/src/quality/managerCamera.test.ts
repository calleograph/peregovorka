import { describe, expect, it, vi } from "vitest";
import type { Room } from "livekit-client";
import { QualityManager } from "./manager";

/** Поддельная камера: три слоя simulcast как у SDK; запоминаем, что записали в отправитель и выключали ли камеру. */
function rig() {
  const enc: Record<string, unknown>[] = [
    { rid: "q", scaleResolutionDownBy: 4, maxBitrate: 200_000, maxFramerate: 15 }, { rid: "h", scaleResolutionDownBy: 2, maxBitrate: 500_000, maxFramerate: 20 },
    { rid: "f", scaleResolutionDownBy: 1, maxBitrate: 1_500_000, maxFramerate: 24 },
  ];
  const sender = { getParameters: () => ({ encodings: enc.map((e) => ({ ...e })) }), setParameters: async (p: { encodings: Record<string, unknown>[] }) => { p.encodings.forEach((e, i) => Object.assign(enc[i], e)); } };
  const setCameraEnabled = vi.fn(async () => undefined);
  const room = { localParticipant: { connectionQuality: "excellent", isCameraEnabled: false, setCameraEnabled, getTrackPublication: (src: string) => (src === "camera" ? { videoTrack: { sender, getSenderStats: async () => [] } } : undefined) } } as unknown as Room;
  const notes: string[] = [];
  const mgr = new QualityManager(room, () => undefined, (t) => notes.push(t));
  return { mgr, enc, setCameraEnabled, notes };
}

describe("менеджер качества: камера по умолчанию из настроек комнаты", () => {
  it("«Минимальное» при публикации камеры реально ограничивает отправку: слои вместе ≤ 100 кбит/с, ≤ 5 кадров/с", async () => {
    const r = rig();
    await r.mgr.setManual("min");
    await r.mgr.onCameraPublished();
    expect(r.enc.reduce((a, e) => a + (e.maxBitrate as number), 0)).toBeLessThanOrEqual(100_000);
    expect(r.enc.every((e) => (e.maxFramerate as number) <= 5)).toBe(true);
    expect(r.enc[2].scaleResolutionDownBy).toBe(4);
  });

  it("выбор участника «Высокое» после «Минимального» возвращает исходные параметры слоёв", async () => {
    const r = rig();
    await r.mgr.setManual("min");
    await r.mgr.onCameraPublished();
    await r.mgr.setManual("high");
    expect(r.enc[2]).toMatchObject({ maxBitrate: 1_500_000, maxFramerate: 24, scaleResolutionDownBy: 1 });
    expect(r.enc[1]).toMatchObject({ maxFramerate: 20, scaleResolutionDownBy: 2 });
  });

  it("«Только звук»: включённая участником камера сразу выключается, пока не выбрано качество, и участнику сказано, что делать (раньше камера шла в полном качестве)", async () => {
    const r = rig();
    await r.mgr.setManual("off");
    await r.mgr.onCameraPublished();
    expect(r.setCameraEnabled).toHaveBeenCalledWith(false);
    expect(r.notes.at(-1)).toMatch(/Только звук.*Качество видео/);
    expect(r.enc[2].maxBitrate).toBe(1_500_000);                // слои не трогались: видео не идёт вовсе
  });

  it("«Автоматически» и обычные уровни камеру не выключают", async () => {
    const r = rig();
    await r.mgr.setManual(null);
    await r.mgr.onCameraPublished();
    await r.mgr.setManual("medium");
    await r.mgr.onCameraPublished();
    expect(r.setCameraEnabled).not.toHaveBeenCalled();
  });
});
