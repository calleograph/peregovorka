import { describe, expect, it } from "vitest";
import { idleMinutes, idleWarnAt } from "./idleEnd";

const MIN = 60_000;

describe("забытая встреча: когда предупреждать", () => {
  it("один и без действий 30 минут — предупреждение; что наступит позже, то и решает", () => {
    expect(idleWarnAt(30, 0, 0)).toBe(30 * MIN);                       // вошёл и сразу отошёл
    expect(idleWarnAt(30, 0, 12 * MIN)).toBe(42 * MIN);                // работал до 12-й минуты: ещё 30 минут без действий
    expect(idleWarnAt(30, 20 * MIN, 0)).toBe(50 * MIN);                // остался один на 20-й минуте: 30 минут одиночества
  });
  it("выключено (0) и не одиночная встреча — никогда", () => {
    expect(idleWarnAt(0, 0, 0)).toBeNull();
    expect(idleWarnAt(30, null, 0)).toBeNull();
  });
  it("минуты: из комнаты; прежний сервер — 30 или выключено", () => {
    expect(idleMinutes({ idle_minutes: 45, idle_end: "standard" })).toBe(45);
    expect(idleMinutes({ idle_minutes: 0 })).toBe(0);
    expect(idleMinutes({ idle_end: "off" })).toBe(0);
    expect(idleMinutes({ idle_end: "standard" })).toBe(30);
    expect(idleMinutes(undefined)).toBe(0);
  });
});
