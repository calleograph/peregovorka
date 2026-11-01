import { describe, expect, it } from "vitest";
import type { PrivacyLabResult } from "./api";
import { addException, context, labFindings } from "./labFindings";

const source = "[00:01] Иванов Иван: Угу. Кузнецов придёт. Позже Кузнецов ушёл. Угу.";
const at = (s: string, from = 0) => source.indexOf(s, from);
const res = {
  source, text: "", ms: 1, profile: "basic", stats: {}, warnings: [], info: {},
  replacements: [
    { start: at("Кузнецов"), end: at("Кузнецов") + 8, category: "person", rule: "ner", original: "Кузнецов", replacement: "Лицо 2", evidence: "фамилия или имя по словарю" },
    { start: at("Кузнецов", 40), end: at("Кузнецов", 40) + 8, category: "person", rule: "ner", original: "Кузнецов", replacement: "Лицо 2", evidence: "фамилия или имя по словарю" },
    { start: 0, end: 3, category: "email", rule: "email", original: "x", replacement: "[почта]" },
  ],
  candidates: [{ start: at("Угу"), end: at("Угу") + 3, original: "Угу", reason: "междометие" }, { start: at("Угу", 30), end: at("Угу", 30) + 3, original: "Угу", reason: "междометие" }],
} as PrivacyLabResult;

describe("лаборатория: люди и спорные кандидаты", () => {
  it("группирует по фрагменту и основанию, считает вхождения, показывает контекст первого", () => {
    const { people, disputed } = labFindings(res);
    expect(people).toHaveLength(1);
    expect(people[0]).toMatchObject({ original: "Кузнецов", replacement: "Лицо 2", note: "фамилия или имя по словарю", count: 2 });
    expect(people[0].context.match).toBe("Кузнецов");
    expect(people[0].context.before.endsWith("Угу. ")).toBe(true);
    expect(disputed).toEqual([expect.objectContaining({ original: "Угу", note: "междометие", count: 2 })]);
  });

  it("контекст — одной строкой и с многоточием на обрезанных краях", () => {
    expect(context("а\nб В в\nг", 4, 5, 2)).toEqual({ before: "…б ", match: "В", after: " в…" });
  });

  it("исключение добавляется без повтора и без пустых строк", () => {
    expect(addException(["Сбер", "", " "], "угу")).toEqual(["Сбер", "угу"]);
    expect(addException(["Угу"], "угу")).toEqual(["Угу"]);
    expect(addException(undefined, "Хм")).toEqual(["Хм"]);
  });
});
