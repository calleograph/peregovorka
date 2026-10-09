import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(__dirname, "..");
const html = readFileSync(resolve(root, "index.html"), "utf8");
const exists = (href: string) => existsSync(resolve(root, "public", href.replace(/^\//, "")));

describe("значки и манифест сайта", () => {
  it("все значки из index.html существуют в public", () => {
    const hrefs = [...html.matchAll(/<link[^>]+href="(\/[^"]+)"/g)].map((m) => m[1]).filter((h) => !h.startsWith("/src/"));
    expect(hrefs.length).toBeGreaterThanOrEqual(6);
    for (const h of hrefs) expect(exists(h), h).toBe(true);
  });
  it("манифест — корректный JSON, значки (в том числе maskable) на месте, размеры совпадают с файлами", () => {
    const m = JSON.parse(readFileSync(resolve(root, "public/manifest.webmanifest"), "utf8"));
    expect(m.name).toBe("Peregovorka");
    expect(m.icons.some((i: { purpose: string }) => i.purpose === "maskable")).toBe(true);
    for (const i of m.icons as { src: string; sizes: string }[]) {
      expect(exists(i.src), i.src).toBe(true);
      const png = readFileSync(resolve(root, "public", i.src.replace(/^\//, "")));
      expect(`${png.readUInt32BE(16)}x${png.readUInt32BE(20)}`).toBe(i.sizes);
    }
  });
  it("значок-вариант «идёт встреча» и ico лежат рядом, сервис закрыт от индексации", () => {
    for (const f of ["favicon-live.svg", "favicon-live-32.png", "favicon.ico", "apple-touch-icon.png", "robots.txt"]) expect(exists(f), f).toBe(true);
    expect(html).toContain('name="robots"');
    expect(readFileSync(resolve(root, "public/robots.txt"), "utf8")).toContain("Disallow: /");
  });
});
