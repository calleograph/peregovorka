import { describe, expect, it } from "vitest";
import { ADMIN_QUICK, adminHref, focusable, nextIndex, tabFromSearch, type MenuItem } from "./navMenu";

describe("клавиатурная навигация по меню", () => {
  it("стрелки идут по кругу, Home/End — к краям", () => {
    expect(nextIndex("ArrowDown", -1, 3)).toBe(0);
    expect(nextIndex("ArrowDown", 2, 3)).toBe(0);
    expect(nextIndex("ArrowUp", 0, 3)).toBe(2);
    expect(nextIndex("ArrowUp", -1, 3)).toBe(2);
    expect(nextIndex("Home", 1, 3)).toBe(0);
    expect(nextIndex("End", 1, 3)).toBe(2);
  });
  it("прочие клавиши и пустое меню не обрабатываются", () => {
    expect(nextIndex("a", 0, 3)).toBeNull();
    expect(nextIndex("ArrowDown", 0, 0)).toBeNull();
  });
  it("фокус не попадает на разделители, пояснения и отключённые пункты", () => {
    const items: MenuItem[] = [
      { kind: "text", key: "t", label: "версия" }, { kind: "link", key: "a", label: "A", to: "/a" }, { kind: "divider", key: "d" },
      { kind: "action", key: "b", label: "B", onSelect: () => undefined, disabled: true }, { kind: "action", key: "c", label: "C", onSelect: () => undefined },
    ];
    expect(focusable(items)).toEqual([1, 4]);
  });
});

describe("ссылки в администрирование", () => {
  it("вкладка кодируется в адресе и читается обратно только из известных", () => {
    expect(adminHref("sip")).toBe("/admin?tab=sip");
    expect(tabFromSearch("?tab=sip", ["sip", "system"])).toBe("sip");
    expect(tabFromSearch("?tab=evil", ["sip"])).toBeNull();
    expect(tabFromSearch("", ["sip"])).toBeNull();
  });
  it("быстрое меню короткое и содержит новые разделы", () => {
    expect(ADMIN_QUICK.length).toBeLessThanOrEqual(7);
    expect(ADMIN_QUICK.map((x) => x.tab)).toEqual(expect.arrayContaining(["system", "updates", "llm", "sip"]));
  });
});
