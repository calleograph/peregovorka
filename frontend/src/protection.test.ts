import { describe, expect, it } from "vitest";
import { protectionOf } from "./protection";

describe("уровень защиты комнаты", () => {
  it("красная надпись «ШИФРОВАНИЕ ВКЛЮЧЕНО» только при реально включённом шифровании, E2EE называется только при E2EE", () => {
    const store = protectionOf({ https: true, storage: true, e2ee: false });
    expect(store).toMatchObject({ tone: "red", label: "🔒 ШИФРОВАНИЕ ВКЛЮЧЕНО", kind: "хранилище" });
    expect(JSON.stringify(store)).not.toMatch(/сквозное \(E2EE\)/);
    expect(store.rows.find((r) => r.title.startsWith("Сквозное"))?.ok).toBe(false);
    const e2ee = protectionOf({ https: true, storage: false, e2ee: true });
    expect(e2ee).toMatchObject({ tone: "red", kind: "сквозное (E2EE)" });
    expect(protectionOf({ https: true, storage: true, e2ee: true }).kind).toBe("сквозное (E2EE) + хранилище");
  });

  it("без шифрования красной надписи нет: только «защищённое соединение» или предупреждение", () => {
    expect(protectionOf({ https: true, storage: false, e2ee: false })).toMatchObject({ tone: "plain", label: "🔒 Защищённое соединение" });
    const http = protectionOf({ https: false, storage: false, e2ee: false });
    expect(http.tone).toBe("warn");
    expect(http.label).not.toContain("ШИФРОВАНИЕ ВКЛЮЧЕНО");
  });

  it("пояснение честно говорит, что обычные звонки не сквозные, а запись при E2EE зависит от политики", () => {
    expect(protectionOf({ https: true, storage: true, e2ee: false }).rows[1].text).toContain("не сквозное");
    expect(protectionOf({ https: true, storage: false, e2ee: true, e2eeRecorder: false }).rows[3].text).toContain("недоступны");
    expect(protectionOf({ https: true, storage: false, e2ee: true, e2eeRecorder: true }).rows[3].text).toContain("доверенный компонент");
  });
});
