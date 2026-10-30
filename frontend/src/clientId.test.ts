import { beforeAll, describe, expect, it, vi } from "vitest";

let clientId: () => string, renewClientId: () => string;
beforeAll(async () => {
  const m = new Map<string, string>();
  vi.stubGlobal("sessionStorage", { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) });
  ({ clientId, renewClientId } = await import("./api"));
});

describe("идентификатор вкладки", () => {
  it("стабилен в пределах вкладки и подходит серверу (8–64 символа [A-Za-z0-9_-])", () => {
    const a = clientId();
    expect(clientId()).toBe(a);
    expect(a).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
  });
  it("renewClientId выдаёт новый идентификатор, который затем и используется (вход после DUPLICATE_IDENTITY — отдельное подключение)", () => {
    const a = clientId();
    const b = renewClientId();
    expect(b).not.toBe(a);
    expect(b).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(clientId()).toBe(b);
  });
});
