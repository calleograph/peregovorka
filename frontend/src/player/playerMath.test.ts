import { describe, expect, it } from "vitest";
import { activeCues, clampRect, countMatches, currentIndex, decodePeaks, matchRanges, momentLink, parseMoment, quoteOf, defaultRect, parsePrefs, reducePeaks, searchCues, timeAtX, xAtTime, type Cue } from "./playerMath";

const cues: Cue[] = [
  { id: 1, start: 1, end: 3, speaker: "Алиса", text: "Предлагаю начать" },
  { id: 2, start: 5, end: 7, speaker: "Борис", text: "Согласен, но нужен бюджет" },
  { id: 3, start: 6, end: 6.8, speaker: "Алиса", text: "Одновременно" },
  { id: 4, start: 20, end: 22, speaker: "Борис", text: "Итог" },
];

describe("волна", () => {
  it("пики декодируются и сводятся к столбикам: максимум группы, 0…1", () => {
    const p = decodePeaks(btoa(String.fromCharCode(0, 255, 10, 20)));
    expect([...p]).toEqual([0, 255, 10, 20]);
    expect(reducePeaks(p, 2)).toEqual([1, 20 / 255]);
    expect(reducePeaks(p, 4)).toEqual([0, 1, 10 / 255, 20 / 255]);
    expect(reducePeaks(new Uint8Array(0), 3)).toEqual([0, 0, 0]);
    expect(reducePeaks(new Uint8Array([200]), 4).every((v) => v === 200 / 255)).toBe(true);
  });
  it("время и положение пересчитываются в обе стороны и не выходят за границы", () => {
    expect(timeAtX(50, 100, 60)).toBe(30);
    expect(timeAtX(-10, 100, 60)).toBe(0);
    expect(timeAtX(500, 100, 60)).toBe(60);
    expect(timeAtX(10, 0, 60)).toBe(0);
    expect(xAtTime(15, 200, 60)).toBe(50);
    expect(xAtTime(1, 200, 0)).toBe(0);
  });
});

describe("субтитры", () => {
  it("номер текущей реплики — последняя начавшаяся", () => {
    expect(currentIndex(cues, 0)).toBe(-1);
    expect(currentIndex(cues, 1)).toBe(0);
    expect(currentIndex(cues, 5.5)).toBe(1);
    expect(currentIndex(cues, 100)).toBe(3);
    expect(currentIndex([], 5)).toBe(-1);
  });
  it("в паузе старый текст исчезает, при одновременной речи видны обе реплики", () => {
    expect(activeCues(cues, 0.5)).toEqual([]);
    expect(activeCues(cues, 2).map((c) => c.id)).toEqual([1]);
    expect(activeCues(cues, 3.5).map((c) => c.id)).toEqual([1]);          // небольшая задержка после конца
    expect(activeCues(cues, 4.5)).toEqual([]);                            // пауза: текста нет
    expect(activeCues(cues, 6.2).map((c) => c.id)).toEqual([2, 3]);       // двое говорят одновременно
    expect(activeCues(cues, 6.2, 0.8, 1).map((c) => c.id)).toEqual([3]);
    expect(activeCues(cues, 15)).toEqual([]);
    expect(activeCues(cues, 21).map((c) => c.id)).toEqual([4]);
  });
  it("после перемотки текущая реплика определяется только временем, без «памяти» о прошлой позиции", () => {
    const a = activeCues(cues, 21).map((c) => c.id);
    const b = activeCues(cues, 2).map((c) => c.id);
    const c2 = activeCues(cues, 21).map((c) => c.id);
    expect([a, b, c2]).toEqual([[4], [1], [4]]);
  });
  it("поиск по тексту и по имени, без учёта регистра", () => {
    expect(searchCues(cues, "бюджет")).toEqual([1]);
    expect(searchCues(cues, "АЛИСА")).toEqual([0, 2]);
    expect(searchCues(cues, "  ")).toEqual([]);
    expect(searchCues(cues, "нет такого")).toEqual([]);
  });
});

describe("окно плеера", () => {
  it("окно нельзя утащить целиком за экран: заголовок остаётся доступным", () => {
    const r = clampRect({ x: -5000, y: -50, w: 500, h: 300 }, 1366, 768);
    expect(r.x).toBe(90 - 500);
    expect(r.y).toBe(0);
    const far = clampRect({ x: 9999, y: 9999, w: 500, h: 300 }, 1366, 768);
    expect(far.x).toBe(1366 - 90);
    expect(far.y).toBe(768 - 40);
    expect(clampRect({ x: 100, y: 100, w: 500, h: 300 }, 1366, 768)).toEqual({ x: 100, y: 100, w: 500, h: 300 });
  });
  it("окно больше экрана сжимается, положение по умолчанию — правый нижний угол", () => {
    expect(clampRect({ x: 0, y: 0, w: 5000, h: 5000 }, 800, 600).w).toBe(800);
    const d = defaultRect(1366, 768, "mini");
    expect(d.x + d.w).toBeLessThanOrEqual(1366);
    expect(d.y + d.h).toBeLessThanOrEqual(768);
    expect(d.x).toBeGreaterThan(683);
  });
  it("сохранённые настройки читаются безопасно: мусор → значения по умолчанию, скорость только из списка", () => {
    expect(parsePrefs(null)).toEqual({ volume: 1, muted: false, speed: 1, cc: false, transcript: false, follow: true });
    expect(parsePrefs("{не json")).toEqual(parsePrefs(null));
    expect(parsePrefs(JSON.stringify({ volume: 7, speed: 3, cc: true, follow: false }))).toMatchObject({ volume: 1, speed: 1, cc: true, follow: false });
    expect(parsePrefs(JSON.stringify({ speed: 1.5, volume: 0.3, muted: true })).speed).toBe(1.5);
  });
});

describe("поиск в стенограмме: русский язык, фразы, подсветка", () => {
  const c = (id: number, speaker: string, text: string): Cue => ({ id, start: id * 10, end: id * 10 + 5, speaker, text });
  const cues = [c(0, "Алиса", "Утвердили бюджет, а ещё — план."), c(1, "Борис", "Ёлка стоит в углу"), c(2, "Алиса", "Бюджет по проекту.")];
  it("слово, фраза, регистр, «ё» и пунктуация", () => {
    expect(searchCues(cues, "БЮДЖЕТ")).toEqual([0, 2]);
    expect(searchCues(cues, "бюджет а еще")).toEqual([0]);
    expect(searchCues(cues, "елка")).toEqual([1]);
    expect(searchCues(cues, "бюджет, а ещё")).toEqual([0]);
    expect(searchCues(cues, "…")).toEqual([]);
  });
  it("фильтр по участнику", () => {
    expect(searchCues(cues, "бюджет", "Борис")).toEqual([]);
    expect(searchCues(cues, "бюджет", "Алиса")).toEqual([0, 2]);
  });
  it("участки подсветки указывают на исходный текст и считаются по всем вхождениям", () => {
    const r = matchRanges("Бюджет: бюджет и БЮДЖЕТЫ", "бюджет");
    expect(r.map(([a, b]) => "Бюджет: бюджет и БЮДЖЕТЫ".slice(a, b))).toEqual(["Бюджет", "бюджет", "БЮДЖЕТ"]);
    expect(matchRanges("ёлка", "ЕЛКА")).toEqual([[0, 4]]);
    expect(countMatches(cues, [0, 2], "бюджет")).toBe(2);
  });
  it("цитата и ссылка на момент", () => {
    expect(quoteOf(cues[0], (t) => `0:${t}`)).toBe("[0:0] Алиса: Утвердили бюджет, а ещё — план.");
    expect(momentLink("https://x", "m", "r", 3725.9)).toBe("https://x/history/m?rec=r&t=3725");
    expect(parseMoment("?rec=11111111-1111-4111-8111-111111111111&t=62")).toEqual({ rec: "11111111-1111-4111-8111-111111111111", t: 62 });
    expect(parseMoment("?rec=../x&t=1")).toBeNull();
    expect(parseMoment("?rec=11111111-1111-4111-8111-111111111111&t=-5")?.t).toBe(0);
  });
});

import { fmtTime } from "../mediaPlayerMath";
describe("многочасовые записи", () => {
  it("время цитаты и ссылка на третьем часу", () => {
    const c: Cue = { id: 1, start: 3 * 3600 + 25 * 60 + 7, end: 3 * 3600 + 25 * 60 + 12, speaker: "Алиса", text: "Итог" };
    expect(quoteOf(c, fmtTime)).toBe("[3:25:07] Алиса: Итог");
    expect(momentLink("https://x", "m", "r", c.start)).toBe("https://x/history/m?rec=r&t=12307");
  });
});
