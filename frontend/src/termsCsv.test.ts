import { describe, expect, it } from "vitest";
import { mergeTerms, parseTermsCsv, termsToCsv } from "./termsCsv";

describe("словарь обезличивания в CSV", () => {
  it("выгрузка прежнего обезличивателя «значение,метка»: метка — своя замена", () => {
    const { terms, skipped } = parseTermsCsv("значение,метка\nПроект Альбатрос,Проект-А\n\"ООО «Ромашка», филиал\",Контрагент\n,пусто\n");
    expect(terms).toEqual([{ term: "Проект Альбатрос", category: "custom", replacement: "Проект-А" },
                           { term: "ООО «Ромашка», филиал", category: "custom", replacement: "Контрагент" }]);
    expect(skipped).toBe(1);
  });

  it("своя выгрузка загружается обратно без потерь, в том числе кавычки и точка с запятой", () => {
    const src = [{ term: "Альфа \"Плюс\"", category: "project", replacement: "" }, { term: "srv-01; резерв", category: "host", replacement: "сервер учёта" }];
    expect(parseTermsCsv(termsToCsv(src)).terms).toEqual(src);
  });

  it("разделитель «;» из Excel и повторы при объединении", () => {
    const { terms } = parseTermsCsv("термин;категория;замена\r\nАльфа;organization;\r\nБета;project;Проект Б\r\n");
    expect(terms.map((t) => t.category)).toEqual(["organization", "project"]);
    const merged = mergeTerms([{ term: "альфа", category: "custom", replacement: "" }], terms);
    expect(merged.added).toBe(1);
    expect(merged.terms.map((t) => t.term)).toEqual(["альфа", "Бета"]);
  });
});
