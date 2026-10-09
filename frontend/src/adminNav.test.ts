import { describe, expect, it } from "vitest";
import { collapseAll, expandAll, loadCollapsed, saveCollapsed, toggleGroup } from "./adminNav";

const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } }; };

describe("свёрнутые разделы меню администрирования", () => {
  it("по умолчанию всё развёрнуто, состояние запоминается", () => {
    const st = mem();
    expect([...loadCollapsed(st)]).toEqual([]);
    saveCollapsed(new Set(["Хранилища", "Журналы"]), st);
    expect([...loadCollapsed(st)].sort()).toEqual(["Журналы", "Хранилища"]);
  });
  it("переключение раздела, свернуть всё, развернуть всё", () => {
    const titles = ["Состояние", "Интеграции", "Система"];
    let s = toggleGroup(new Set(), "Интеграции");
    expect(s.has("Интеграции")).toBe(true);
    s = toggleGroup(s, "Интеграции");
    expect(s.size).toBe(0);
    expect([...collapseAll(titles)]).toEqual(titles);
    expect(expandAll().size).toBe(0);
  });
  it("испорченные данные в хранилище не ломают меню", () => {
    expect(loadCollapsed({ getItem: () => "{не json" }).size).toBe(0);
    expect([...loadCollapsed({ getItem: () => JSON.stringify(["А", 5, null]) })]).toEqual(["А"]);
    expect(loadCollapsed(null).size).toBe(0);
  });
});
