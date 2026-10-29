import { describe, expect, it } from "vitest";
import { placeMenu } from "./menuPlace";

describe("положение меню", () => {
  it("у курсора, если помещается", () => {
    expect(placeMenu(100, 100, 200, 150, 1000, 800)).toEqual({ left: 100, top: 100 });
  });
  it("над кнопкой нижняя кромка меню стоит на месте при любой высоте", () => {
    for (const h of [120, 300, 420]) {
      const p = placeMenu(300, 700, 220, h, 1280, 800, 8, true);
      expect(p.top + h).toBe(700);
    }
  });
  it("над кнопкой: не вылезает за верх окна и за правый край", () => {
    expect(placeMenu(1200, 300, 220, 500, 1280, 800, 8, true)).toEqual({ left: 980, top: 8 });
  });
  it("без «над»: поднимается, чтобы поместиться снизу", () => {
    expect(placeMenu(10, 700, 100, 200, 800, 800)).toEqual({ left: 10, top: 592 });
  });
});
