import { describe, expect, it } from "vitest";
import { addDays, buildCreateBody, dayKey, defaultForm, describe as describeEvent, formError, groupByDay, layoutDay, newKey, nextSlot, rangeFor, shift, startOfWeek, title, type CalEvent, type CreateForm } from "./calendarView";

const ev = (over: Partial<CalEvent> = {}): CalEvent => ({ id: "1", source: "exchange", title: "Встреча", start: "2026-10-28T07:00:00Z", end: "2026-10-28T08:00:00Z", all_day: false, kind: "meeting", status: "scheduled", recurring: false,
  location: null, etag: null, room_id: null, mine: false, join_url: null, guest_url: null, editable: false, ...over });
const local = (y: number, m: number, d: number, h = 0, mi = 0) => new Date(y, m - 1, d, h, mi);

describe("периоды", () => {
  it("неделя начинается с понедельника", () => {
    expect(dayKey(startOfWeek(local(2026, 10, 28)))).toBe("2026-10-26");      // среда
    expect(dayKey(startOfWeek(local(2026, 11, 1)))).toBe("2026-10-26");       // воскресенье принадлежит прошлой неделе
    expect(dayKey(startOfWeek(local(2026, 10, 26)))).toBe("2026-10-26");
  });
  it("день, неделя, месяц, список", () => {
    const d = local(2026, 10, 28, 15, 30);
    expect(rangeFor("day", d)).toEqual({ from: local(2026, 10, 28), to: local(2026, 10, 29) });
    expect(rangeFor("week", d)).toEqual({ from: local(2026, 10, 26), to: local(2026, 11, 2) });
    const m = rangeFor("month", d);                                            // октябрь 2026: 1-е — четверг, 31-е — суббота
    expect([dayKey(m.from), dayKey(addDays(m.to, -1))]).toEqual(["2026-09-28", "2026-11-01"]);
    expect(rangeFor("list", d)).toEqual({ from: local(2026, 10, 28), to: local(2026, 11, 27) });
  });
  it("сетка месяца всегда целыми неделями и не длиннее запроса сервера (45 суток)", () => {
    for (let m = 1; m <= 12; m++) {
      const r = rangeFor("month", local(2027, m, 15));
      const days = Math.round((r.to.getTime() - r.from.getTime()) / 86400000);
      expect(days % 7).toBe(0);
      expect(days).toBeLessThanOrEqual(42);
    }
  });
  it("переход вперёд и назад, в том числе через границу месяца и года", () => {
    expect(dayKey(shift("day", local(2026, 12, 31), 1))).toBe("2027-01-01");
    expect(dayKey(shift("week", local(2026, 10, 28), -1))).toBe("2026-10-21");
    expect(dayKey(shift("month", local(2026, 1, 31), 1))).toBe("2026-02-01");
    expect(dayKey(shift("month", local(2026, 1, 15), -1))).toBe("2025-12-01");
  });
  it("заголовки периодов по-русски", () => {
    expect(title("month", local(2026, 10, 5))).toBe("Октябрь 2026");
    expect(title("day", local(2026, 10, 28))).toBe("среда, 28 октября 2026");
    expect(title("week", local(2026, 10, 28))).toBe("26 октября – 1 ноября 2026");
    expect(title("week", local(2026, 10, 14))).toBe("12–18 октября 2026");
  });
});

describe("события по дням", () => {
  it("событие через полночь показывается в обоих днях своими кусками", () => {
    const e = ev({ start: local(2026, 10, 28, 22, 0).toISOString(), end: local(2026, 10, 29, 1, 0).toISOString() });
    const g = groupByDay([e], local(2026, 10, 28), local(2026, 10, 30));
    const a = g.get("2026-10-28")![0], b = g.get("2026-10-29")![0];
    expect(a.continues && !a.continued).toBe(true);
    expect(b.continued && !b.continues).toBe(true);
    expect(g.get("2026-10-29")).toHaveLength(1);
  });
  it("событие вне периода не попадает; порядок внутри дня — по началу", () => {
    const g = groupByDay([ev({ id: "b", title: "Б", start: local(2026, 10, 28, 12).toISOString(), end: local(2026, 10, 28, 13).toISOString() }), ev({ id: "a", title: "А", start: local(2026, 10, 28, 9).toISOString(), end: local(2026, 10, 28, 10).toISOString() }),
      ev({ id: "x", start: local(2026, 12, 1, 9).toISOString(), end: local(2026, 12, 1, 10).toISOString() })], local(2026, 10, 28), local(2026, 10, 29));
    expect(g.get("2026-10-28")!.map((s) => s.ev.id)).toEqual(["a", "b"]);
    expect([...g.keys()]).toEqual(["2026-10-28"]);
  });
  it("битые даты пропускаются, а не ломают календарь", () => {
    expect(groupByDay([ev({ start: "вчера", end: "завтра" })], local(2026, 10, 28), local(2026, 10, 29)).get("2026-10-28")).toEqual([]);
  });
});

describe("раскладка суток", () => {
  const at = (h: number, m: number, h2: number, m2: number, id: string) => ev({ id, start: local(2026, 10, 28, h, m).toISOString(), end: local(2026, 10, 28, h2, m2).toISOString() });
  const place = (events: CalEvent[]) => layoutDay(groupByDay(events, local(2026, 10, 28), local(2026, 10, 29)).get("2026-10-28")!, local(2026, 10, 28));
  it("непересекающиеся — в одну колонку на всю ширину", () => {
    const p = place([at(9, 0, 10, 0, "a"), at(10, 0, 11, 0, "b")]);
    expect(p.map((x) => [x.col, x.cols])).toEqual([[0, 1], [0, 1]]);
    expect(p[0].top).toBeCloseTo(9 / 24);
    expect(p[0].height).toBeCloseTo(1 / 24);
  });
  it("пересекающиеся делят ширину", () => {
    const p = place([at(9, 0, 11, 0, "a"), at(10, 0, 12, 0, "b"), at(10, 30, 11, 30, "c")]);
    expect(Math.max(...p.map((x) => x.cols))).toBe(3);
    expect(new Set(p.map((x) => x.col)).size).toBe(3);
  });
  it("после пересечения следующая группа снова на всю ширину", () => {
    const p = place([at(9, 0, 10, 0, "a"), at(9, 30, 10, 30, "b"), at(14, 0, 15, 0, "c")]);
    expect(p.find((x) => x.slice.ev.id === "c")!.cols).toBe(1);
  });
  it("очень короткое событие не тоньше 20 минут; событие на весь день в сетку часов не попадает", () => {
    const p = place([at(9, 0, 9, 5, "short"), ev({ id: "all", all_day: true })]);
    expect(p).toHaveLength(1);
    expect(p[0].height).toBeCloseTo(20 / 1440);
  });
});

describe("подписи", () => {
  it("событие описывается не только цветом: тип, название, время, статус", () => {
    const t = describeEvent(ev({ kind: "series", recurring: true, status: "live", title: "Планёрка", start: local(2026, 10, 28, 10).toISOString(), end: local(2026, 10, 28, 10, 45).toISOString() }));
    expect(t).toBe("Серия. Планёрка. 10:00–10:45. повторяется. Идёт");
  });
});

describe("создание встречи", () => {
  const form = (over: Partial<CreateForm> = {}): CreateForm => ({ ...defaultForm(local(2026, 10, 29, 10, 0)), title: "Бюджет", ...over });
  it("ближайший слот — следующие полчаса", () => {
    expect(nextSlot(local(2026, 10, 28, 9, 10))).toEqual(local(2026, 10, 28, 9, 30));
    expect(nextSlot(local(2026, 10, 28, 9, 30))).toEqual(local(2026, 10, 28, 10, 0));
    expect(nextSlot(local(2026, 10, 28, 23, 45))).toEqual(local(2026, 10, 29, 0, 0));
  });
  it("проверка формы: название, время, дни недели, окончание", () => {
    const now = local(2026, 10, 28, 9);
    expect(formError(form({ title: "  " }), now)).toMatch(/название/);
    expect(formError(form({ date: "2026-10-01" }), now)).toMatch(/закончилась/);
    expect(formError(form({ repeat: "weekly", days: [] }), now)).toMatch(/день недели/);
    expect(formError(form({ repeat: "daily", endMode: "date", until: "2026-10-01" }), now)).toMatch(/не раньше/);
    expect(formError(form({ repeat: "daily", endMode: "count", count: 0 }), now)).toMatch(/от 1 до 999/);
    expect(formError(form(), now)).toBeNull();
  });
  it("тело запроса: UTC, пояс, ключ, участники без выдуманных адресов", () => {
    const b = buildCreateBody(form({ minutes: 45, attendees: [{ id: "u1", ref: null, login: "bob", name: "Bob" }, { id: null, ref: null, login: null, name: "Внешний", email: "ext@example.org", optional: true }] }), "calKEY", "Europe/Moscow");
    expect(b.start).toBe(local(2026, 10, 29, 10).toISOString());
    expect(b.end).toBe(local(2026, 10, 29, 10, 45).toISOString());
    expect(b).toMatchObject({ key: "calKEY", subject: "Бюджет", tz: "Europe/Moscow", conference: true, guest_link: false });
    expect(b.attendees).toEqual([{ id: "u1", ref: null, login: "bob", optional: false }, { email: "ext@example.org", name: "Внешний", optional: true }]);
    expect("recurrence" in b).toBe(false);
  });
  it("повторение: дни недели, окончание по числу или дате", () => {
    expect(buildCreateBody(form({ repeat: "weekly", days: [2, 0], endMode: "count", count: 6 }), "k", "UTC").recurrence).toEqual({ type: "weekly", interval: 1, days: [0, 2], count: 6 });
    expect(buildCreateBody(form({ repeat: "monthly", endMode: "date", until: "2027-01-31" }), "k", "UTC").recurrence).toEqual({ type: "monthly", interval: 1, end: "2027-01-31" });
    expect(buildCreateBody(form({ repeat: "daily" }), "k", "UTC").recurrence).toEqual({ type: "daily", interval: 1 });
  });
  it("гостевая ссылка отправляется, только если есть конференция", () => {
    expect(buildCreateBody(form({ conference: false, guest: true }), "k", "UTC").guest_link).toBe(false);
    expect(buildCreateBody(form({ conference: true, guest: true }), "k", "UTC").guest_link).toBe(true);
  });
  it("ключ запроса подходит серверу (латиница и цифры, 16–70 знаков) и меняется", () => {
    const k = newKey();
    expect(k).toMatch(/^[A-Za-z0-9_-]{16,70}$/);
    expect(newKey()).not.toBe(k);
  });
});
