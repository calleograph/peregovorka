import { describe, expect, it } from "vitest";
import { secretToSend } from "./common";

describe("пароли в формах", () => {
  it("пустое поле и служебный пробел — «не менять», новое значение уходит как есть", () => {
    expect(secretToSend("")).toBeUndefined();
    expect(secretToSend(" ")).toBeUndefined();
    expect(secretToSend("новый-Пароль-1")).toBe("новый-Пароль-1");
  });
});
