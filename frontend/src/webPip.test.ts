import { describe, expect, it } from "vitest";
import { lastSpeakerOf, pipFocus, type PipCand } from "./webPip";

const p = (identity: string, o: Partial<PipCand> = {}): PipCand => ({ identity, local: false, screen: false, cam: false, speaking: false, ...o });

describe("плавающее окно: кого показывать", () => {
  it("чужая демонстрация главнее говорящего, своя — не показывается", () => {
    expect(pipFocus([p("me", { local: true, screen: true }), p("boris", { cam: true, speaking: true }), p("anna", { screen: true })], "boris")).toEqual({ identity: "anna", source: "screen" });
    expect(pipFocus([p("me", { local: true, screen: true, cam: true }), p("boris")], null)).toEqual({ identity: "boris", source: "none" });
  });
  it("говорящий с камерой, затем любой говорящий", () => {
    expect(pipFocus([p("boris", { speaking: true }), p("vera", { cam: true, speaking: true })], null)?.identity).toBe("vera");
    expect(pipFocus([p("boris", { speaking: true }), p("vera", { cam: true })], "vera")?.identity).toBe("boris");
  });
  it("в тишине — последний говоривший, пока он во встрече; ушёл — следующий с камерой", () => {
    const room = [p("me", { local: true, cam: true }), p("boris"), p("vera", { cam: true })];
    expect(pipFocus(room, "boris")?.identity).toBe("boris");
    expect(pipFocus(room.filter((x) => x.identity !== "boris"), "boris")).toEqual({ identity: "vera", source: "camera" });
  });
  it("вошёл первым (один) — после прихода собеседника показывается он, а не сам участник", () => {
    expect(pipFocus([p("me", { local: true })], null)?.identity).toBe("me");
    expect(pipFocus([p("me", { local: true }), p("boris", { cam: true })], "me")?.identity).toBe("boris");
  });
  it("последний говоривший — только собеседник", () => {
    expect(lastSpeakerOf([p("me", { local: true, speaking: true }), p("boris")], null)).toBeNull();
    expect(lastSpeakerOf([p("me", { local: true }), p("boris", { speaking: true })], "vera")).toBe("boris");
    expect(lastSpeakerOf([p("me", { local: true }), p("boris")], "vera")).toBe("vera");
  });
  it("один — сам участник; никого — ничего", () => {
    expect(pipFocus([p("me", { local: true, cam: true })], null)).toEqual({ identity: "me", source: "camera" });
    expect(pipFocus([], null)).toBeNull();
  });
});
