import { describe, expect, it } from "vitest";
import { applyLocalMute, loadLocalMuted, saveLocalMuted, toggleLocalMute } from "./localMute";

const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } }; };

describe("локальное «заглушить для себя»", () => {
  it("переключение не меняет исходное множество и возвращает участника обратно", () => {
    const a = new Set<string>();
    const b = toggleLocalMute(a, "u-1");
    expect(a.size).toBe(0); expect(b.has("u-1")).toBe(true);
    expect(toggleLocalMute(b, "u-1").has("u-1")).toBe(false);
  });
  it("сохраняется на время встречи, отдельно по встречам (переживает переподключение)", () => {
    const st = mem();
    saveLocalMuted("m1", new Set(["u-1", "u-2"]), st);
    expect([...loadLocalMuted("m1", st)].sort()).toEqual(["u-1", "u-2"]);
    expect(loadLocalMuted("m2", st).size).toBe(0);
  });
  it("испорченное или недоступное хранилище не ломает комнату", () => {
    expect(loadLocalMuted("m1", { getItem: () => "{не json" }).size).toBe(0);
    expect([...loadLocalMuted("m1", { getItem: () => JSON.stringify(["a", 5, null]) })]).toEqual(["a"]);
    expect(loadLocalMuted("m1", null).size).toBe(0);
    expect(() => saveLocalMuted("m1", new Set(["x"]), null)).not.toThrow();
  });
  it("применяется только к звуку нужных участников", () => {
    const mk = (id: string) => ({ dataset: { identity: id }, muted: false });
    const els = [mk("u-1"), mk("u-2")];
    const root = { querySelectorAll: () => ({ forEach: (f: (e: unknown) => void) => els.forEach(f) }) } as unknown as ParentNode;
    applyLocalMute(root, new Set(["u-2"]));
    expect(els.map((e) => e.muted)).toEqual([false, true]);
    applyLocalMute(root, new Set());
    expect(els.map((e) => e.muted)).toEqual([false, false]);
  });
});
