import { describe, expect, it } from "vitest";
import { humanSeconds } from "./components/MapTab";

describe("время формирования карты", () => {
  it("минуты и секунды целиком", () => {
    expect(humanSeconds(276)).toBe("4 мин 36 с");
    expect(humanSeconds(45.4)).toBe("45 с");
    expect(humanSeconds(120)).toBe("2 мин");
    expect(humanSeconds(3900)).toBe("1 ч 5 мин");
  });
});
