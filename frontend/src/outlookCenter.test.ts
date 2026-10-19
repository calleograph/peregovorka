import { describe, expect, it } from "vitest";
import { inspectManifest, isInternalHost, manifestUrls, runClientChecks } from "./outlookCenter";

const XML = (id: string, url = "https://meet.example.org") => `<?xml version="1.0"?><OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1"><Id>${id}</Id><Version>1.0.0</Version>
<bt:Url id="a" DefaultValue="${url}/outlook/commands.html"/><bt:Url id="b" DefaultValue="${url}/outlook/icon-64.png"/></OfficeApp>`;

describe("центр установки Outlook: проверки в браузере", () => {
  it("находит внутренние адреса и не-HTTPS", () => {
    expect(["localhost", "10.1.2.3", "10.0.0.7", "10.0.0.8", "intranet-host"].every(isInternalHost)).toBe(true);
    expect(["meet.example.org", "198.51.100.8"].some(isInternalHost)).toBe(false);
  });
  it("манифест: чужой идентификатор и внутренний адрес — замечания, нормальный — без них", () => {
    expect(manifestUrls(XML("x"))).toHaveLength(2);
    expect(inspectManifest(XML("abc"), "abc").problems).toEqual([]);
    expect(inspectManifest(XML("zzz"), "abc").problems.join()).toContain("не совпадает");
    expect(inspectManifest(XML("abc", "http://10.0.0.5"), "abc").problems.join()).toMatch(/не по HTTPS|Внутренние/);
  });
  it("перенаправление манифеста — ошибка; недоступный API — ошибка с понятной причиной", async () => {
    const f = (async (url: string) => {
      if (String(url).endsWith("/config")) throw new TypeError("net");
      if (String(url).endsWith("manifest.xml")) return { type: "opaqueredirect", status: 0, ok: false } as unknown as Response;
      return { ok: true, status: 200 } as Response;
    }) as unknown as typeof fetch;
    const r = await runClientChecks("https://meet.example.org", "abc", f);
    expect(r.find((c) => c.key === "api")?.ok).toBe(false);
    expect(r.find((c) => c.key === "manifest")?.message).toContain("перенаправление");
  });
});
