import { describe, expect, it } from "vitest";
import { applyFavicon, pageTitle } from "./pageTitle";

describe("заголовок вкладки", () => {
  it("у каждой страницы свой заголовок с названием продукта", () => {
    expect(pageTitle("/")).toBe("Переговорки · Peregovorka");
    expect(pageTitle("/rooms/abc")).toBe("Комната · Peregovorka");
    expect(pageTitle("/history/")).toBe("История встреч · Peregovorka");
    expect(pageTitle("/history/5")).toBe("Встреча · Peregovorka");
    expect(pageTitle("/admin")).toBe("Администрирование · Peregovorka");
    expect(pageTitle("/profile")).toBe("Личный кабинет · Peregovorka");
  });
  it("без входа — «Вход», а публичные страницы не зависят от входа", () => {
    expect(pageTitle("/", false)).toBe("Вход · Peregovorka");
    expect(pageTitle("/privacy", false)).toBe("Обработка данных · Peregovorka");
    expect(pageTitle("/guest/xyz", false)).toBe("Гостевой вход · Peregovorka");
    expect(pageTitle("/неизвестно")).toBe("Peregovorka");
  });
});

describe("значок вкладки", () => {
  const link = (type: string, sizes: string | null, href: string) => { const a: Record<string, string | null> = { type, sizes, href }; return { getAttribute: (k: string) => a[k] ?? null, setAttribute: (k: string, v: string) => { a[k] = v; } }; };
  it("во время встречи подставляется вариант с красной точкой, после — возвращается обычный", () => {
    const links = [link("image/svg+xml", null, "/favicon.svg"), link("image/png", "32x32", "/favicon-32.png"), link("image/png", "16x16", "/favicon-16.png")];
    const doc = { querySelectorAll: () => links } as unknown as Document;
    const hrefs = () => links.map((l) => l.getAttribute("href"));
    applyFavicon(true, doc);
    expect(hrefs()).toEqual(["/favicon-live.svg", "/favicon-live-32.png", "/favicon-16.png"]);
    applyFavicon(false, doc);
    expect(hrefs()).toEqual(["/favicon.svg", "/favicon-32.png", "/favicon-16.png"]);
  });
});
