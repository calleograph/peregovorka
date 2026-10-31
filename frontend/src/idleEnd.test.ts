import { describe, expect, it } from "vitest";
import { idleWarnAt } from "./idleEnd";

const MIN = 60_000;

describe("забытая встреча: когда предупреждать", () => {
  it("один 15 минут и без действий 10 — предупреждение; что наступит позже, то и решает", () => {
    expect(idleWarnAt("standard", 0, 0)).toBe(15 * MIN);                         // вошёл и сразу отошёл: ждём 15 минут одиночества
    expect(idleWarnAt("standard", 0, 12 * MIN)).toBe(22 * MIN);                  // работал до 12-й минуты: ещё 10 минут без действий
    expect(idleWarnAt(undefined, 0, 0)).toBe(15 * MIN);                          // старый сервер без режима — стандартный
  });
  it("осторожный режим ждёт дольше, выключенный и не одиночная встреча — никогда", () => {
    expect(idleWarnAt("careful", 0, 0)).toBe(30 * MIN);
    expect(idleWarnAt("off", 0, 0)).toBeNull();
    expect(idleWarnAt("standard", null, 0)).toBeNull();
  });
});
