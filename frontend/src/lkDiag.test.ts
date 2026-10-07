import { describe, expect, it } from "vitest";
import { describeProbe, failStage, probeSignal, redactSecrets, safeUrl, signalHttpUrl } from "./lkDiag";

const TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJ2aWRlbyI6eyJyb29tIjoibS0xIn19.abcdefghijklmnop";

describe("redactSecrets", () => {
  it("токен и join_request из URL не попадают в текст, а длинное значение заменено размером", () => {
    const url = `wss://meet.test/livekit/rtc/v1?access_token=${TOKEN}&auto_subscribe=1&join_request=${"A".repeat(2000)}&sdk=js`;
    const r = redactSecrets(`WebSocket connection to '${url}' failed: ERR_CONNECTION_RESET`);
    expect(r).not.toContain(TOKEN);
    expect(r).not.toContain("AAAAAAAAAA");
    expect(r).toContain("access_token=<скрыто");
    expect(r).toContain("join_request=<скрыто, 2000 симв.>");
    expect(r).toContain("auto_subscribe=1");
    expect(r).toContain("ERR_CONNECTION_RESET");
  });
  it("JWT и Bearer вне URL, ограничение длины", () => {
    expect(redactSecrets(`Authorization: Bearer ${TOKEN}`)).not.toContain(TOKEN);
    expect(redactSecrets(`ошибка ${TOKEN} конец`)).toContain("<jwt скрыт>");
    const long = redactSecrets("x".repeat(1000), 100);
    expect(long.length).toBeLessThan(150);
    expect(long).toContain("+900 симв.");
    expect(redactSecrets(undefined)).toBe("");
  });
});

describe("адреса", () => {
  it("safeUrl убирает параметры и логин", () => {
    expect(safeUrl(`wss://u:p@meet.test/livekit/?access_token=${TOKEN}#x`)).toBe("wss://meet.test/livekit");
  });
  it("HTTP-адрес сигнального сервера по wss", () => {
    expect(signalHttpUrl("wss://meet.test/livekit")).toBe("https://meet.test/livekit/");
    expect(signalHttpUrl("ws://127.0.0.1:7880")).toBe("http://127.0.0.1:7880/");
  });
});

describe("probeSignal", () => {
  it("успех и HTTP-ошибка", async () => {
    expect(await probeSignal("wss://m.test/livekit", 1000, (async () => ({ ok: true, status: 200 })) as unknown as typeof fetch)).toMatchObject({ ok: true, status: 200 });
    const bad = await probeSignal("wss://m.test/livekit", 1000, (async () => ({ ok: false, status: 502 })) as unknown as typeof fetch);
    expect(bad).toMatchObject({ ok: false, status: 502 });
    expect(describeProbe(bad)).toContain("502");
  });
  it("сброс соединения и таймаут различимы", async () => {
    const reset = await probeSignal("wss://m.test/livekit", 1000, (async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch);
    expect(reset).toMatchObject({ ok: false, error: "network_error" });
    expect(describeProbe(reset)).toContain("сброшено");
    const hang = await probeSignal("wss://m.test/livekit", 20, ((_u: string, init: RequestInit) => new Promise((_ok, bad) => {
      init.signal?.addEventListener("abort", () => bad(Object.assign(new Error("aborted"), { name: "AbortError" })));
    })) as unknown as typeof fetch);
    expect(hang).toMatchObject({ ok: false, error: "timeout" });
    expect(describeProbe(hang)).toContain("не ответил");
  });
});

describe("failStage", () => {
  it("ICE по тексту, сигнал по тексту и по этапу", () => {
    expect(failStage(new Error("could not establish pc connection"))).toBe("ice");
    expect(failStage(new Error("WebSocket connection failed: ERR_CONNECTION_RESET"))).toBe("signal");
    expect(failStage(new Error("Failed to fetch"), "server")).toBe("signal");
    expect(failStage(new Error("что-то странное"), "media")).toBe("ice");
    expect(failStage(new Error("что-то странное"))).toBe("unknown");
  });
});
