import { describe, expect, it } from "vitest";
import { bitrixFields } from "./fields";

describe("Bitrix24: поля настроек", () => {
  const byName = Object.fromEntries(bitrixFields.map((f) => [f.name, f]));

  it("адрес webhook — секрет (не показывается и не логируется)", () => {
    expect(byName.webhook_url.type).toBe("secret");
  });

  it("приоритеты заданы для каждого поля профиля, включая фото", () => {
    for (const n of ["display_name", "email", "title", "department", "phone", "avatar"]) expect(byName[`priority_${n}`]?.type).toBe("text");
  });

  it("имена полей совпадают с группой настроек на сервере", () => {
    const expected = ["enabled", "portal_url", "webhook_url", "allow_http", "timeout", "verify_tls", "use_corporate_ca", "use_title", "use_department", "use_phone", "use_photos", "cache_hours"];
    for (const n of expected) expect(byName[n], n).toBeDefined();
  });

  it("в примерах только условные адреса", () => {
    const text = JSON.stringify(bitrixFields);
    expect(text).not.toMatch(/https?:\/\/(?!portal\.example\.com)[a-z0-9][a-z0-9.-]*\.[a-z]{2,}/i);
  });
});

describe("Публичный API: поля настроек", () => {
  it("совпадают с группой настроек на сервере", async () => {
    const { publicApiFields } = await import("./fields");
    const names = publicApiFields.map((f) => f.name);
    for (const n of ["enabled", "rate_read", "rate_write", "rate_ai", "rate_download", "log_retention_days", "max_page_size"]) expect(names).toContain(n);
    expect(publicApiFields.find((f) => f.name === "enabled")?.type).toBe("bool");
  });
});
