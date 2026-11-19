import { beforeAll, describe, expect, it } from "vitest";
import viewerSource from "../public/mapview/viewer.js?raw";

// Файл просмотра — обычный скрипт для браузера; в тесте он выполняется без document и отдаёт только чистые функции.
type Util = {
  squarify: (items: { id: string; value: number }[], w: number, h: number) => { id: string; x: number; y: number; w: number; h: number }[];
  timelineBars: (topics: { id: string; segments: { start_s: number; end_s: number }[] }[], total: number) => { topic: string; left: number; width: number }[];
  ticks: (total: number) => number[]; duration: (s: number) => string; clockOffset: (s: number) => string; plural: (n: number, a: string, b: string, c: string) => string;
};
let U: Util;
beforeAll(() => {
  new Function(viewerSource)();      // eslint-disable-line no-new-func -- тест: запускаем собственный файл просмотра
  U = (globalThis as unknown as { PgMapUtil: Util }).PgMapUtil;
});

describe("карта разговора: геометрия", () => {
  it("блоки занимают всю площадь пропорционально времени и не пересекаются", () => {
    const items = [{ id: "a", value: 22 }, { id: "b", value: 10 }, { id: "c", value: 5 }, { id: "d", value: 3 }, { id: "e", value: 1 }];
    const r = U.squarify(items, 800, 340);
    expect(r).toHaveLength(5);
    const total = items.reduce((s, i) => s + i.value, 0);
    for (const i of items) {
      const c = r.find((x) => x.id === i.id)!;
      expect(c.w * c.h / (800 * 340)).toBeCloseTo(i.value / total, 6);
    }
    expect(r.reduce((s, c) => s + c.w * c.h, 0)).toBeCloseTo(800 * 340, 3);
    for (const c of r) {
      expect(c.x).toBeGreaterThanOrEqual(-1e-6);
      expect(c.y).toBeGreaterThanOrEqual(-1e-6);
      expect(c.x + c.w).toBeLessThanOrEqual(800 + 1e-6);
      expect(c.y + c.h).toBeLessThanOrEqual(340 + 1e-6);
    }
    for (let i = 0; i < r.length; i++) {
      for (let j = i + 1; j < r.length; j++) {
        const a = r[i], b = r[j];
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x), oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(ox > 1e-6 && oy > 1e-6).toBe(false);
      }
    }
  });
  it("пустые и нулевые темы не ломают раскладку", () => {
    expect(U.squarify([], 100, 100)).toEqual([]);
    expect(U.squarify([{ id: "x", value: 0 }], 100, 100)).toEqual([]);
    expect(U.squarify([{ id: "x", value: 5 }], 100, 50)).toEqual([{ id: "x", x: 0, y: 0, w: 100, h: 50 }]);
  });
  it("шкала: ширина отрезка пропорциональна времени, короткий отрезок остаётся видимым", () => {
    const bars = U.timelineBars([{ id: "t", segments: [{ start_s: 0, end_s: 300 }, { start_s: 2820, end_s: 2825 }] }], 3600);
    expect(bars[0].left).toBe(0);
    expect(bars[0].width).toBeCloseTo(300 / 3600 * 100, 6);
    expect(bars[1].left).toBeCloseTo(2820 / 3600 * 100, 6);
    expect(bars[1].width).toBeGreaterThanOrEqual(0.4);
    expect(U.timelineBars([], 0)).toEqual([]);
  });
  it("деления шкалы и подписи", () => {
    expect(U.ticks(1800)).toEqual([0, 300, 600, 900, 1200, 1500, 1800]);
    expect(U.ticks(5400).length).toBeLessThanOrEqual(9);
    expect((U.ticks as unknown as (t: number, m: number, p: number) => number[])(2880, 8, 16 * 3600 + 9 * 60)).toEqual([60, 660, 1260, 1860, 2460]);    // встреча с 16:09 → деления 16:10, 16:20…
    expect(U.duration(22 * 60)).toBe("22 мин");
    expect(U.duration(75 * 60)).toBe("1 ч 15 мин");
    expect(U.duration(30)).toBe("30 с");
    expect(U.clockOffset(34 * 60 + 18)).toBe("00:34:18");
    expect(U.plural(2, "участник", "участника", "участников")).toBe("участника");
  });
});

describe("карта разговора: названия разделов и честные подписи", () => {
  it("используются понятные названия, а прежние оборотные заголовки не остались", () => {
    for (const t of ["Ход встречи", "Темы обсуждения", "Активность участников", "Подтверждающие реплики", "Когда обсуждали", "Связанные обсуждения"]) expect(viewerSource).toContain(t);
    for (const t of ["На что ушла встреча", "Как шла встреча", "Кто говорил", "Реплики-источники", "Фрагменты обсуждения", "Соседние по времени", "Размер блока — потраченное время"]) expect(viewerSource).not.toContain(t);
  });
  it("длинное пояснение про площадь блока заменено подсказкой, вывод модели назван выводом модели", () => {
    expect(viewerSource).toContain("Площадь блока отражает продолжительность обсуждения");
    expect(viewerSource).toContain("Вывод языковой модели");
    expect(viewerSource).not.toContain("AI-интерпретация");
  });
  it("служебные сведения (модель, повторы, токены) на странице карты не выводятся", () => {
    expect(viewerSource).not.toMatch(/Фрагментов: |повторов: |Модель: /);
  });
  it("не разобранные участки отмечены на шкале и названы в тексте", () => {
    expect(viewerSource).toContain("Не удалось разобрать");
    expect(viewerSource).toContain('el("span", "gap")');
  });
});

type Topic = { id: string; title: string; summary: string; category: string; total_s: number; segments: { start_s: number; end_s: number }[]; speakers: { name: string; seconds: number }[];
  decisions?: unknown[]; tasks?: unknown[]; questions?: unknown[]; proposals?: unknown[]; related?: string[] };
type Map = { meeting: { title: string; started_at: string; timezone: string; duration_s: number }; topics: Topic[]; unassigned?: Record<string, unknown[]>; outcome?: { text: string; basis: string } | null };
type U2 = {
  matchTopic: (t: Topic, f: { q: string; cat: string | null; person: string | null }) => boolean;
  sortTopics: (t: Topic[], mode: string) => Topic[];
  collect: (d: Map) => Record<string, { item: { text: string }; topic: Topic | null }[]>;
  counts: (d: Map) => Record<string, number>;
  outcome: (d: Map) => { text: string; basis: string };
  summaryText: (d: Map) => string;
  flow: (t: Topic[]) => { cat: string; len: number }[];
  relatedPairs: (t: Topic[]) => string[][];
};
const T = (id: string, cat: string, segs: [number, number][], extra: Partial<Topic> = {}): Topic => ({
  id, title: "Тема " + id, summary: "", category: cat, total_s: segs.reduce((s, x) => s + x[1] - x[0], 0), segments: segs.map(([a, b]) => ({ start_s: a, end_s: b })),
  speakers: [{ name: "Анна", seconds: 10 }], ...extra,
});
const MAP: Map = {
  meeting: { title: "Пилот", started_at: "2026-11-11T11:00:00Z", timezone: "UTC", duration_s: 1620 },
  topics: [
    T("a", "security", [[0, 240], [1200, 1400]], { title: "Безопасность доступа", decisions: [{ text: "Выдавать результаты через ответственного", sec: 100 }], related: ["b"],
      tasks: [{ text: "Собрать список ответственных", assignee: "Алексей К.", deadline: null, sec: 200 }] }),
    T("b", "infra", [[240, 900]], { speakers: [{ name: "Ёлкин", seconds: 30 }], questions: [{ text: "Где хранить образы?", sec: 500 }], related: ["a"] }),
    T("c", "security", [[900, 1200]], { proposals: [{ text: "Можно попробовать блокировку выпуска", sec: 950 }] }),
  ],
  unassigned: { tasks: [{ text: "Разослать запись", sec: 1500 }] },
};
describe("карта разговора: данные новой компоновки", () => {
  const V = () => (globalThis as unknown as { PgMapUtil: U2 }).PgMapUtil;
  it("поиск, категория и участник применяются вместе; «ё» и регистр не мешают", () => {
    const f = (q: string, cat: string | null = null, person: string | null = null) => MAP.topics.filter((t) => V().matchTopic(t, { q, cat, person })).map((t) => t.id);
    expect(f("")).toEqual(["a", "b", "c"]);
    expect(f("ОТВЕТСТВЕННОГО")).toEqual(["a"]);                       // по тексту решения
    expect(f("елкин")).toEqual(["b"]);                                // по участнику, «ё» = «е»
    expect(f("", "security")).toEqual(["a", "c"]);
    expect(f("блокировку", "infra")).toEqual([]);
    expect(f("", null, "Ёлкин")).toEqual(["b"]);
  });
  it("возвращение к теме — несколько участков одной темы на шкале", () => {
    const bars = (globalThis as unknown as { PgMapUtil: Util }).PgMapUtil.timelineBars(MAP.topics, 1620);
    expect(bars.filter((b) => b.topic === "a")).toHaveLength(2);
  });
  it("решения, задачи, предложения и вопросы собраны по встрече, предложение не считается решением", () => {
    const c = V().collect(MAP), n = V().counts(MAP);
    expect(c.decisions.map((x) => x.item.text)).toEqual(["Выдавать результаты через ответственного"]);
    expect(c.tasks.map((x) => x.topic?.id ?? null)).toEqual(["a", null]);
    expect(n).toMatchObject({ topics: 3, returns: 1, decisions: 1, tasks: 2, proposals: 1, questions: 1, owned: 1, dated: 0 });
    expect(V().sortTopics(MAP.topics, "long").map((t) => t.id)).toEqual(["b", "a", "c"]);
  });
  it("итог: из карты, а у старой карты без итога — честно по числу пунктов", () => {
    expect(V().outcome({ ...MAP, outcome: { text: "Согласована модель", basis: "ai" } }).basis).toBe("ai");
    expect(V().outcome(MAP)).toEqual({ text: "Зафиксировано решений: 1, поручений: 2, открытых вопросов: 1.", basis: "code" });
    expect(V().outcome({ ...MAP, topics: [T("x", "general", [[0, 60]])], unassigned: {} }).text).toMatch(/^Решений и поручений в стенограмме не зафиксировано/);
  });
  it("текст «Копировать итоги»: неизвестные ответственный и срок названы, а не додуманы", () => {
    const s = V().summaryText(MAP);
    expect(s).toContain("Собрать список ответственных — ответственный: Алексей К.; срок: не назван");
    expect(s).toContain("Разослать запись — ответственный: не назван; срок: не назван");
    expect(s).toContain("Предложено, не согласовано:");
  });
  it("последовательность категорий и пары связанных тем без повторов", () => {
    expect(V().flow(MAP.topics).map((x) => x.cat)).toEqual(["security", "infra", "security"]);
    expect(V().relatedPairs(MAP.topics)).toEqual([["a", "b"]]);
  });
});
