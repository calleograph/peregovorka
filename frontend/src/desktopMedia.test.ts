import { describe, expect, it } from "vitest";
import { parseDesktopMedia, pickByLabel } from "./desktopMedia";

describe("parseDesktopMedia", () => {
  it("принимает значения приложения и подставляет безопасные по умолчанию", () => {
    expect(parseDesktopMedia({ mic: "Гарнитура", speaker: "", camera: "Камера", noise: "off", echo: false, agc: true })).toEqual({ mic: "Гарнитура", speaker: "", camera: "Камера", noise: "off", echo: false, agc: true });
    expect(parseDesktopMedia({ noise: "turbo", mic: 5 })).toEqual({ mic: "", speaker: "", camera: "", noise: "enhanced", echo: true, agc: true });
  });
  it("не объект — нет данных", () => {
    for (const v of [null, undefined, "x", 3]) expect(parseDesktopMedia(v)).toBeNull();
  });
  it("слишком длинное название обрезается", () => {
    expect(parseDesktopMedia({ mic: "я".repeat(500) })?.mic.length).toBe(200);
  });
});

describe("pickByLabel", () => {
  const list = [{ label: "A", id: 1 }, { label: "B", id: 2 }];
  it("находит по названию, пустое и отсутствующее — null", () => {
    expect(pickByLabel(list, "B")?.id).toBe(2);
    expect(pickByLabel(list, "")).toBeNull();
    expect(pickByLabel(list, "C")).toBeNull();
  });
});
