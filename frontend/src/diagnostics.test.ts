import { describe, expect, it } from "vitest";
import { JoinTimeline, RateMeter, metricsBody } from "./diagnostics";

describe("хронология входа", () => {
  it("вычисляет интервалы с теми же именами, что и серверные метрики", () => {
    const t = new JoinTimeline();
    t.mark("click", 1000); t.mark("joinStart", 1010); t.mark("joinEnd", 1050);
    t.mark("connectStart", 1060); t.mark("signalConnected", 1800); t.mark("mediaConnected", 3200);
    t.mark("active", 3250); t.mark("micStart", 3260); t.mark("micPublished", 3700);
    expect(t.metrics()).toEqual({
      join_api_ms: 40, signaling_connect_ms: 740, ice_connect_ms: 1400, participant_active_ms: 2250, microphone_publish_ms: 440,
    });
  });

  it("повторная отметка не переписывает первую; без отметок значения не выдумываются", () => {
    const t = new JoinTimeline();
    t.mark("click", 10); t.mark("click", 999);
    expect(t.metrics().participant_active_ms).toBeUndefined();
    t.mark("active", 110);
    expect(t.metrics().participant_active_ms).toBe(100);
  });

  it("время на текущем этапе растёт, пока этап не завершён", () => {
    const t = new JoinTimeline();
    t.mark("connectStart", 1000);
    expect(t.stageMs("server", 4500)).toBe(3500);
    expect(t.stageMs("media", 4500)).toBe(0);
  });
});

describe("битрейт", () => {
  it("считается по приращению байт", () => {
    const m = new RateMeter();
    expect(m.kbps("a", 0, 0)).toBeUndefined();
    expect(m.kbps("a", 125_000, 1000)).toBe(1000);
    expect(m.kbps("a", 125_000, 2000)).toBe(0);
    expect(m.kbps("a", 100, 3000)).toBeUndefined(); // счётчик сбросился (переподключение)
  });
});

describe("тело метрик", () => {
  it("содержит только числа и метки, без лишнего", () => {
    const b = metricsBody("m1", { at: 1, rttMs: 20, lossPct: 0.4, candidate: "udp/host", screenOut: { fps: 14.8, bitrateKbps: 2500, width: 1920, height: 1080, limitReason: "bandwidth" } },
      { join_api_ms: 40 });
    expect(b).toMatchObject({ meeting_id: "m1", join_api_ms: 40, rtt_ms: 20, packet_loss_pct: 0.4, candidate: "udp/host", screen: { fps: 14.8, limit_reason: "bandwidth" } });
  });
});
