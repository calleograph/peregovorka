import { describe, expect, it } from "vitest";
import { describeMediaError } from "./mediaErrors";

const err = (name: string, message = "") => Object.assign(new Error(message), { name });
const d = (e: unknown, a: Parameters<typeof describeMediaError>[1]) => describeMediaError(e, a, { secureContext: true });

describe("причины ошибок вместо общего «Не удалось переключить устройство»", () => {
  it("каждая причина получает своё понятное сообщение", () => {
    const msgs = new Set<string>();
    for (const [n, a] of [["NotAllowedError", "mic"], ["NotFoundError", "mic"], ["NotReadableError", "camera"], ["OverconstrainedError", "camera"],
      ["SecurityError", "screen"], ["NotAllowedError", "screen"], ["NotReadableError", "screen"], ["NotFoundError", "screen"]] as const) {
      const r = d(err(n), a);
      expect(r.reason).toBe(n);
      expect(r.message).not.toMatch(/^Не удалось переключить устройство$/);
      msgs.add(r.message);
    }
    expect(msgs.size).toBe(8);
  });

  it("экран: отмена выбора — не авария", () => {
    expect(d(err("AbortError"), "screen").benign).toBe(true);
    expect(d(err("NotAllowedError", "Permission denied by user"), "screen").benign).toBe(true);
    expect(d(err("NotReadableError"), "screen").benign).toBeFalsy();
  });

  it("не HTTPS — объясняется причина", () => {
    const r = describeMediaError(err("NotAllowedError"), "mic", { secureContext: false });
    expect(r.reason).toBe("InsecureContext");
    expect(r.message).toContain("HTTPS");
  });

  it("ошибки сети и публикации LiveKit различаются по тексту", () => {
    expect(d(new Error("could not establish signal connection"), "connect").reason).toBe("SignalFailed");
    expect(d(new Error("publishing timed out"), "screen").reason).toBe("Timeout");
    expect(d(new Error("insufficient permissions to publish: can_publish_sources"), "screen").reason).toBe("PublishNotAllowed");
    expect(d(new Error("DUPLICATE_IDENTITY"), "connect").reason).toBe("DuplicateIdentity");
  });

  it("неизвестная ошибка содержит имя и текст, а не пустую фразу", () => {
    const r = d(err("WeirdError", "boom"), "device");
    expect(r.message).toContain("WeirdError");
    expect(r.message).toContain("boom");
  });
});

describe("подключение: сброс соединения и этапы", () => {
  it("ERR_CONNECTION_RESET объясняется как сетевая проблема (VPN/MTU/прокси/файрвол), а не ошибка приложения", () => {
    const i = describeMediaError(new Error("WebSocket connection to 'wss://m.test/livekit/rtc' failed: ERR_CONNECTION_RESET"), "connect");
    expect(i.reason).toBe("ConnectionReset");
    expect(i.message).toMatch(/сброшено на сетевом уровне/);
    expect(i.message).toMatch(/VPN/);
    expect(i.message).toMatch(/MTU/);
    expect(i.message).toMatch(/прокси/);
  });
  it("таймаут различает сигнальное соединение и ICE", () => {
    const sig = describeMediaError(new Error("timeout"), "connect", { stage: "server" });
    const ice = describeMediaError(new Error("timeout"), "connect", { stage: "media" });
    expect(sig.reason).toBe("Timeout");
    expect(ice.reason).toBe("IceTimeout");
    expect(sig.message).toMatch(/сигнальное соединение/i);
    expect(ice.message).toMatch(/ICE/);
  });
  it("сбой WebSocket называется сигнальным соединением", () => {
    expect(describeMediaError(new Error("websocket closed"), "connect").message).toMatch(/сигнальное соединение \(WebSocket\)/);
  });
});
