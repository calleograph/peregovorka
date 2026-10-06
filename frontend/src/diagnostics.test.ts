import { describe, expect, it } from "vitest";
import { JoinTimeline, metricsBody, newInstanceId } from "./diagnostics";

describe("хронология входа", () => {
  it("вычисляет все интервалы пути входа с теми же именами, что и серверные метрики", () => {
    const t = new JoinTimeline();
    t.mark("click", 1000); t.mark("joinStart", 1010); t.mark("joinEnd", 1034);
    t.mark("wsStart", 1036); t.mark("wsOpen", 1100);
    t.mark("roomCreated", 1040); t.mark("connectStart", 1041); t.mark("gumStart", 1042); t.mark("gumEnd", 1500);
    t.mark("signalConnected", 1181); t.mark("mediaConnected", 1491);
    t.mark("active", 1534); t.mark("micStart", 1535); t.mark("micPublished", 1715);
    expect(t.metrics()).toEqual({
      join_api_ms: 24, room_create_ms: 6, livekit_connect_ms: 450, signaling_connect_ms: 140, ice_connect_ms: 310, participant_active_ms: 534,
      get_user_media_ms: 458, microphone_publish_ms: 180, backend_ws_connect_ms: 64, total_join_ms: 534,
    });
  });

  it("аномалия вида «пять секунд между /join и RTC» видна в числах, а не «на глаз»", () => {
    const t = new JoinTimeline();
    t.mark("click", 0); t.mark("joinStart", 5); t.mark("joinEnd", 30); t.mark("roomCreated", 32); t.mark("connectStart", 33);
    t.mark("signalConnected", 5200); t.mark("mediaConnected", 5600); t.mark("active", 5640);
    const m = t.metrics();
    expect(m.join_api_ms).toBe(25);
    expect(m.signaling_connect_ms).toBeGreaterThan(5000);
    expect(m.total_join_ms).toBeGreaterThan(5000);
  });

  it("повторная отметка не переписывает первую; без отметок значения не выдумываются", () => {
    const t = new JoinTimeline();
    t.mark("click", 10); t.mark("click", 999);
    expect(t.metrics().total_join_ms).toBeUndefined();
    t.mark("active", 110);
    expect(t.metrics().total_join_ms).toBe(100);
  });

  it("время на текущем этапе растёт, пока этап не завершён", () => {
    const t = new JoinTimeline();
    t.mark("connectStart", 1000);
    expect(t.stageMs("server", 4500)).toBe(3500);
    expect(t.stageMs("media", 4500)).toBe(0);
  });
});

describe("идентификатор Room", () => {
  it("короткий и разный для каждого объекта", () => {
    const a = newInstanceId(), b = newInstanceId();
    expect(a).toHaveLength(8);
    expect(a).not.toBe(b);
  });
});

describe("тело метрик", () => {
  it("содержит только числа и метки", () => {
    const b = metricsBody("m1", { at: 1, rttMs: 20, lossPct: 0.4, path: { candidate: "udp/host→srflx" }, screenOut: { fps: 14.8, bitrateKbps: 2500, width: 1920, height: 1080, limitReason: "bandwidth" } },
      { join_api_ms: 40, total_join_ms: 900 });
    expect(b).toMatchObject({ meeting_id: "m1", join_api_ms: 40, total_join_ms: 900, rtt_ms: 20, packet_loss_pct: 0.4, candidate: "udp/host→srflx", screen: { fps: 14.8, limit_reason: "bandwidth" } });
  });
});
