import { describe, expect, it } from "vitest";
import { canStack } from "./stackTables";

describe("canStack", () => {
  it("обычная таблица с подписями превращается в карточки", () => expect(canStack(["Имя", "Роль", ""], [3, 3, 3])).toBe(true));
  it("строка с объединённой ячейкой (−1) оставляет таблицу как есть", () => expect(canStack(["Имя", "Роль"], [2, -1])).toBe(false));
  it("строка-заголовок группы (0) не мешает", () => { expect(canStack(["A", "B"], [0, 2, 2])).toBe(true); expect(canStack(["A", "B"], [0, 0])).toBe(false); });
  it("пустое тело и один столбец не превращаются", () => { expect(canStack(["Имя", "Роль"], [])).toBe(false); expect(canStack(["Имя"], [1])).toBe(false); });
  it("число ячеек обязано совпадать со столбцами шапки", () => expect(canStack(["A", "B", "C"], [3, 2])).toBe(false));
});

// Таблицы документов Glass (протокол, письма) раскладываются своим CSS (.gd-stack), общий механизм карточек их не трогает: иначе у них исчезала бы ширина и подписи.
import { readFileSync } from "node:fs";
describe("таблицы документов", () => {
  it("общий механизм пропускает всё, что внутри .gd", () => expect(readFileSync(new URL("./stackTables.ts", import.meta.url), "utf8")).toContain('t.closest(".gd")'));
  it("у документов свой класс раскладки, не пересекающийся с общим .stack приложения", () => {
    expect(readFileSync(new URL("./components/GlassDoc.tsx", import.meta.url), "utf8")).toContain("gd-table gd-stack");
    expect(readFileSync(new URL("../public/glassdoc.css", import.meta.url), "utf8")).not.toMatch(/\.gd-table\.stack/);
  });
});
