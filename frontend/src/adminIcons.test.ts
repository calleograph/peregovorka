import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ADMIN_ICONS } from "./adminIcons";
import { ICON_NAMES } from "./components/Icons";

// разделы меню берутся из исходника AdminPage: новый раздел без пиктограммы — ошибка
const src = readFileSync(new URL("./pages/AdminPage.tsx", import.meta.url), "utf8");
const ids = [...src.slice(src.indexOf("const GROUPS"), src.indexOf("const KEYWORDS")).matchAll(/\{ id: "([a-z0-9_]+)"/g)].map((m) => m[1]);

describe("пиктограммы меню администрирования", () => {
  it("разделы найдены", () => expect(ids.length).toBeGreaterThan(50));
  it("у каждого раздела есть пиктограмма, и лишних нет", () => expect(Object.keys(ADMIN_ICONS).sort()).toEqual([...ids].sort()));
  it("пиктограммы не повторяются", () => {
    const seen = new Map<string, string>();
    for (const [id, name] of Object.entries(ADMIN_ICONS)) { expect(seen.get(name), `${id} и ${seen.get(name)}: ${name}`).toBeUndefined(); seen.set(name, id); }
  });
  it("каждая пиктограмма есть в наборе", () => { for (const name of Object.values(ADMIN_ICONS)) expect(ICON_NAMES).toContain(name); });
});
