import { describe, expect, it } from "vitest";
import { canStack } from "./stackTables";

describe("canStack", () => {
  it("обычная таблица с подписями превращается в карточки", () => expect(canStack(["Имя", "Роль", ""], [3, 3, 3])).toBe(true));
  it("строка с объединённой ячейкой (−1) оставляет таблицу как есть", () => expect(canStack(["Имя", "Роль"], [2, -1])).toBe(false));
  it("строка-заголовок группы (0) не мешает", () => { expect(canStack(["A", "B"], [0, 2, 2])).toBe(true); expect(canStack(["A", "B"], [0, 0])).toBe(false); });
  it("пустое тело и один столбец не превращаются", () => { expect(canStack(["Имя", "Роль"], [])).toBe(false); expect(canStack(["Имя"], [1])).toBe(false); });
  it("число ячеек обязано совпадать со столбцами шапки", () => expect(canStack(["A", "B", "C"], [3, 2])).toBe(false));
});
