import { describe, expect, it } from "vitest";
import { parseTime } from "./TimeField";

describe("ввод времени в 24-часовом формате", () => {
  it("понимает привычные записи", () => {
    expect(parseTime("9")).toBe("09:00");
    expect(parseTime("930")).toBe("09:30");
    expect(parseTime("0930")).toBe("09:30");
    expect(parseTime("9:5")).toBe("09:05");
    expect(parseTime("09:30")).toBe("09:30");
    expect(parseTime("14.45")).toBe("14:45");
    expect(parseTime("23:59")).toBe("23:59");
    expect(parseTime(" 7: ")).toBe("07:00");
  });
  it("не время — null: 24 часа, 60 минут, буквы, пустое", () => {
    for (const bad of ["24:00", "12:60", "2460", "ab", "", "1:2:3", "12345", "-1"]) expect(parseTime(bad), bad).toBeNull();
  });
});
