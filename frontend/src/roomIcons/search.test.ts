import { describe, expect, it } from "vitest";
import { ROOM_ICON_CATEGORIES, ROOM_ICONS } from "./icons";
import { findIcons, iconByKey } from "./search";

describe("библиотека значков комнат", () => {
  it("достаточно значков, у каждой категории они есть, разметка без активного содержимого", () => {
    expect(ROOM_ICONS.length).toBeGreaterThanOrEqual(100);
    for (const c of ROOM_ICON_CATEGORIES) expect(ROOM_ICONS.some((i) => i.category === c.id), c.id).toBe(true);
    for (const i of ROOM_ICONS) expect(i.body, i.key).not.toMatch(/<script|<style|onload|href=|javascript:/i);
    expect(new Set(ROOM_ICONS.map((i) => i.key)).size).toBe(ROOM_ICONS.length);
  });
  it("поиск по названию, подсказкам и фильтру категорий; регистр и «ё» не важны", () => {
    expect(findIcons("СЕРВЕР").map((i) => i.key)).toContain("server");
    expect(findIcons("щщщ").length).toBe(0);
    expect(findIcons("шестерёнка").some((i) => i.key === "settings")).toBe(true);         // «ё» и «е» равнозначны
    expect(findIcons("шестеренка").some((i) => i.key === "settings")).toBe(true);
    expect(findIcons("защита").some((i) => i.key === "shield")).toBe(true);
    expect(findIcons("", "security").every((i) => i.category === "security")).toBe(true);
    expect(findIcons("сервер", "business").length).toBe(0);
    expect(iconByKey("star")?.name).toBe("Звезда");
    expect(iconByKey("нет")).toBeUndefined();
  });
});
