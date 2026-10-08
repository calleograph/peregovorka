import { describe, expect, it } from "vitest";
import { growHeight } from "./autoGrow";

describe("growHeight", () => {
  it("короткий текст — минимальная высота, без прокрутки", () => { expect(growHeight(20, 40, 140)).toEqual({ height: 40, scroll: false }); });
  it("растёт вместе с текстом", () => { expect(growHeight(96, 40, 140)).toEqual({ height: 96, scroll: false }); });
  it("после предела высота фиксируется и включается прокрутка", () => { expect(growHeight(400, 40, 140)).toEqual({ height: 140, scroll: true }); });
  it("ровно на пределе прокрутки нет", () => { expect(growHeight(140, 40, 140)).toEqual({ height: 140, scroll: false }); });
});
