import { describe, expect, it } from "vitest";
import { audienceCounts, audienceLabel, filterAudience, onStage, sameList, sortAudience, splitStage, windowRange, type AudiencePerson } from "./presentation";

const person = (i: number, over: Partial<AudiencePerson> = {}): AudiencePerson => ({ identity: `u-${i}`, name: `Зритель ${String(i).padStart(4, "0")}`, local: false, mic: false, cam: false, screen: false, ...over });
const crowd = (n: number) => Array.from({ length: n }, (_, i) => person(i));

describe("presentation stage and audience", () => {
  it("only leaders, floor holders and publishers get a tile", () => {
    expect(onStage(person(1))).toBe(false);
    expect(onStage(person(1, { leader: true }))).toBe(true);
    expect(onStage(person(1, { floor: true }))).toBe(true);
    expect(onStage(person(1, { cam: true }))).toBe(true);
    expect(onStage(person(1, { screen: true }))).toBe(true);
    expect(onStage(person(1, { mic: true }))).toBe(true);
  });

  it("regular rooms keep every participant on the stage", () => {
    const all = crowd(5);
    const s = splitStage(all, false);
    expect(s.stage).toBe(all);
    expect(s.audience).toEqual([]);
  });

  it("1000 viewers produce no tiles, a floor holder moves to the stage and back after revoke", () => {
    const people: AudiencePerson[] = [person(0, { leader: true, mic: true }), ...crowd(1000).slice(1)];
    let s = splitStage(people, true);
    expect(s.stage).toHaveLength(1);
    expect(s.audience).toHaveLength(999);
    people[5] = { ...people[5], floor: true };
    s = splitStage(people, true);
    expect(s.stage.map((p) => p.identity)).toEqual(["u-0", "u-5"]);
    people[5] = { ...people[5], floor: false };
    s = splitStage(people, true);
    expect(s.stage).toHaveLength(1);
    expect(s.audience.some((p) => p.identity === "u-5")).toBe(true);
  });

  it("raised hands go first in queue order, then names; own row is first", () => {
    const list = [person(3), person(2, { hand: true, handOrder: 2 }), person(1, { hand: true, handOrder: 1 }), person(9, { local: true })];
    expect(sortAudience(list).map((p) => p.identity)).toEqual(["u-9", "u-1", "u-2", "u-3"]);
  });

  it("search ignores case and ё/е", () => {
    const list = [person(1, { name: "Пётр Иванов" }), person(2, { name: "Анна Петрова" })];
    expect(filterAudience(list, "петр").map((p) => p.name)).toEqual(["Пётр Иванов", "Анна Петрова"]);
    expect(filterAudience(list, "ИВАНОВ")).toHaveLength(1);
    expect(filterAudience(list, "  ")).toBe(list);
    expect(filterAudience(list, "ничего")).toEqual([]);
  });

  it("counts viewers and hands, and declines the word", () => {
    const list = [person(1, { hand: true }), person(2), person(3, { hand: true })];
    expect(audienceCounts(list)).toEqual({ viewers: 3, hands: 2 });
    expect(audienceLabel(1)).toMatch(/1 зритель$/);
    expect(audienceLabel(3)).toMatch(/3 зрителя$/);
    expect(audienceLabel(11)).toMatch(/11 зрителей$/);
    expect(audienceLabel(1000)).toMatch(/зрителей$/);
  });
});

describe("virtual window", () => {
  it("draws only the visible rows of 1000", () => {
    const w = windowRange(1000, 36, 0, 360, 6);
    expect(w.start).toBe(0);
    expect(w.end).toBe(16);
    expect(w.padTop).toBe(0);
    expect(w.padBottom).toBe((1000 - 16) * 36);
  });

  it("moves with scrolling and keeps total height constant", () => {
    const rows = 1000, h = 36;
    for (const top of [0, 1234, 17_000, 35_640]) {
      const w = windowRange(rows, h, top, 360, 6);
      expect(w.end - w.start).toBeLessThanOrEqual(10 + 12 + 2);
      expect(w.padTop + (w.end - w.start) * h + w.padBottom).toBe(rows * h);
      expect(w.start * h).toBeLessThanOrEqual(top);
      expect(w.end * h).toBeGreaterThanOrEqual(Math.min(top + 360, rows * h));
    }
  });

  it("is safe for empty and tiny lists", () => {
    expect(windowRange(0, 36, 0, 360)).toEqual({ start: 0, end: 0, padTop: 0, padBottom: 0 });
    expect(windowRange(3, 36, 5000, 360)).toEqual({ start: 0, end: 3, padTop: 0, padBottom: 0 });
  });
});

describe("no redundant state updates", () => {
  it("treats an unchanged list as the same, detects a changed field", () => {
    const a = crowd(1000), b = a.map((p) => ({ ...p }));
    const keys = ["identity", "name", "mic", "cam", "screen", "floor", "leader", "hand"] as const;
    expect(sameList(a, b, keys)).toBe(true);
    b[500] = { ...b[500], mic: true };
    expect(sameList(a, b, keys)).toBe(false);
    expect(sameList(a, b.slice(1), keys)).toBe(false);
  });

  it("runs the whole pipeline over 1000 people fast enough for a render", () => {
    const people = crowd(1000).map((p, i) => ({ ...p, hand: i % 50 === 0, handOrder: i }));
    const t0 = performance.now();
    for (let k = 0; k < 20; k++) {
      const { audience } = splitStage(people, true);
      const view = filterAudience(sortAudience(audience), "00");
      windowRange(view.length, 36, 400, 360);
    }
    expect((performance.now() - t0) / 20).toBeLessThan(25);
  });
});
