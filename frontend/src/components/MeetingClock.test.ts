import { describe, expect, it } from "vitest";
import { elapsedText, mskText } from "./MeetingClock";

describe("MeetingClock", () => {
  it("длительность: минуты, часы, расхождение часов", () => {
    expect(elapsedText(65_000)).toBe("01:05");
    expect(elapsedText(3_723_000)).toBe("1:02:03");
    expect(elapsedText(-5_000)).toBe("00:00");
  });
  it("время по Москве не зависит от пояса компьютера", () => {
    expect(mskText(new Date("2026-11-20T09:07:00Z"))).toBe("12:07");
  });
});
