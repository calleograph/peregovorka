import { describe, expect, it } from "vitest";
import { BAD_AFTER_MS, DOWN_MS, UP_MS, netDetail, netLabel, stepNet, type NetRaw, type NetShown } from "./tileNet";

const run = (seq: [number, NetRaw][]) => { let s: NetShown | undefined; for (const [t, r] of seq) s = stepNet(s, r, t); return s!; };

describe("индикатор связи на плитке", () => {
  it("четыре уровня, «нет связи» и «нет данных»", () => {
    expect(run([[0, "excellent"]]).level).toBe(4);
    expect(run([[0, "good"]]).level).toBe(3);
    expect(run([[0, "poor"]]).level).toBe(2);
    expect(run([[0, "poor"], [BAD_AFTER_MS, "poor"]]).level).toBe(1);
    expect(run([[0, "lost"]]).lost).toBe(true);
    expect(netLabel(run([[0, "unknown"]]))).toBe("Нет данных о соединении");
  });
  it("кратковременные колебания не вызывают мигания; устойчивое ухудшение показывается через 3 с, улучшение — через 8 с", () => {
    let s = run([[0, "excellent"], [1000, "poor"], [2000, "excellent"]]);
    expect(s.level).toBe(4);
    s = run([[0, "excellent"], [1000, "poor"], [1000 + DOWN_MS, "poor"]]);
    expect(s.level).toBe(2);
    s = stepNet(s, "excellent", 1000 + DOWN_MS + 100);
    expect(s.level).toBe(2);
    s = stepNet(s, "excellent", 1000 + DOWN_MS + 100 + UP_MS);
    expect(s.level).toBe(4);
  });
  it("потеря связи — сразу, восстановление — сразу", () => {
    let s = run([[0, "good"], [500, "lost"]]);
    expect(s.lost).toBe(true);
    s = stepNet(s, "good", 900);
    expect(s.lost).toBe(false);
    expect(s.level).toBe(3);
  });
  it("подсказка различает проблему участника и проблему приёма у вас", () => {
    const ok = run([[0, "excellent"]]);
    expect(netDetail(ok, { lossPct: 7, jitterMs: 40 }, null)).toContain("неполадки на вашей стороне");
    expect(netDetail(run([[0, "poor"]]), { lossPct: 7, jitterMs: 40 }, null)).not.toContain("вашей стороне");
    expect(netDetail(ok, null, { rttMs: 450, lossPct: 5 })).toContain("высокая задержка 450 мс · потеря пакетов 5.0%");
  });
});
