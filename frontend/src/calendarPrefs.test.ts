import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadSession, parseDay, saveCalendars, saveDay, saveMode, saveWork } from "./calendarPrefs";

const store = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }; };

describe("состояние календаря между переходами по разделам", () => {
  beforeEach(() => { vi.stubGlobal("localStorage", store()); vi.stubGlobal("sessionStorage", store()); });
  afterEach(() => vi.unstubAllGlobals());
  it("по умолчанию — неделя, сегодня, основной календарь, 08–19", () => {
    const s = loadSession();
    expect(s.mode).toBe("week"); expect(s.calendars).toEqual(["primary"]); expect(s.work).toEqual({ from: 8, to: 19, onlyWork: false });
    expect(Math.abs(s.anchor.getTime() - Date.now())).toBeLessThan(5000);
  });
  it("вид, дата, календари и рабочие часы восстанавливаются", () => {
    saveMode("month"); saveDay(new Date(2026, 10, 3, 17)); saveCalendars(["primary", "CAL2"]); saveWork({ from: 9, to: 18, onlyWork: true });
    const s = loadSession();
    expect(s.mode).toBe("month"); expect([s.anchor.getFullYear(), s.anchor.getMonth(), s.anchor.getDate()]).toEqual([2026, 10, 3]);
    expect(s.calendars).toEqual(["primary", "CAL2"]); expect(s.work).toEqual({ from: 9, to: 18, onlyWork: true });
  });
  it("испорченные значения не ломают страницу", () => {
    localStorage.setItem("pg:calendarMode", "weird"); localStorage.setItem("pg:calendarCals", "{не json"); localStorage.setItem("pg:calendarWork", "123"); sessionStorage.setItem("pg:calendarDay", "2026-13-45");
    const s = loadSession();
    expect(s.mode).toBe("week"); expect(s.calendars).toEqual(["primary"]); expect(s.work.from).toBe(8);
    expect(parseDay("2026-02-31")).toBeNull(); expect(parseDay("2026-10-27")).not.toBeNull(); expect(parseDay(null)).toBeNull();
  });
  it("браузер без хранилища: страница работает без запоминания", () => {
    vi.stubGlobal("localStorage", { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } });
    vi.stubGlobal("sessionStorage", { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } });
    expect(() => { saveMode("day"); saveDay(new Date()); }).not.toThrow();
    expect(loadSession().mode).toBe("week");
  });
});
