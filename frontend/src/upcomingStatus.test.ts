import { describe, expect, it } from "vitest";
import type { UpcomingMeeting } from "./api";
import { action, dayLabel, meetingState, span, tickMs, whenLabel } from "./upcomingStatus";

const NOW = Date.parse("2026-10-23T12:00:00Z");
const iso = (minFromNow: number) => new Date(NOW + minFromNow * 60_000).toISOString();
const m = (over: Partial<UpcomingMeeting> = {}): UpcomingMeeting => ({
  id: "1", slug: "ol-x", name: "Планёрка", kind: "scheduled", start: iso(60), end: iso(90), opens_at: iso(50), recurring: false, organizer: "Алиса", cancelled: false,
  live: null, last_ended_at: null, invitees: { count: 0, names: [], can_list: true }, mine: true, can_join: true, can_manage: true, has_password: false, url: "/m/x", sync: null, ...over,
});

describe("Предстоящие встречи: отсчёт и состояние", () => {
  it("остаток времени крупными единицами", () => {
    expect(span(2 * 3600_000 + 15 * 60_000)).toBe("2 ч 15 мин");
    expect(span(18 * 60_000 + 59_000)).toBe("18 мин");
    expect(span(45_000)).toBe("45 сек");
    expect(span(3 * 86400_000 + 4 * 3600_000)).toBe("3 д 4 ч");
    expect(span(-5)).toBe("0 сек");
  });

  it("до начала: «Запланирована» → «Скоро начнётся» за 15 мин или после открытия входа; последняя минута — в секундах", () => {
    expect(meetingState(m({ start: iso(135), end: iso(165), opens_at: iso(125) }), NOW)).toEqual({ state: "scheduled", text: "Через 2 ч 15 мин" });
    expect(meetingState(m({ start: iso(18), opens_at: iso(8) }), NOW)).toEqual({ state: "scheduled", text: "Через 18 мин" });
    expect(meetingState(m({ start: iso(12), opens_at: iso(2) }), NOW)).toEqual({ state: "soon", text: "Через 12 мин" });
    expect(meetingState(m({ start: iso(40), opens_at: iso(-5) }), NOW).state).toBe("soon");
    expect(meetingState(m({ start: new Date(NOW + 45_000).toISOString() }), NOW).text).toBe("Начало через 45 сек");
  });

  it("наступление времени начала не значит «Идёт»: «Идёт» — только по фактической встрече", () => {
    const st = meetingState(m({ start: iso(-3), end: iso(30) }), NOW, "UTC");
    expect(st.state).toBe("soon");
    expect(st.text).toBe("Начало было в 11:57 · участники ещё не вошли");
    expect(meetingState(m({ start: iso(-3), live: { started_at: iso(-12), participants: 3 } }), NOW)).toEqual({ state: "live", text: "Встреча идёт · 12 мин" });
    expect(meetingState(m({ live: { started_at: iso(0), participants: 1 } }), NOW).text).toBe("Встреча только началась");
  });

  it("завершена: прошло плановое окончание или встреча уже прошла и закончилась; отменена — всегда «Отменена»", () => {
    expect(meetingState(m({ start: iso(-90), end: iso(-30) }), NOW, "UTC")).toEqual({ state: "ended", text: "Завершилась в 11:30" });
    expect(meetingState(m({ start: iso(-40), end: iso(20), last_ended_at: iso(-2) }), NOW, "UTC")).toEqual({ state: "ended", text: "Завершилась в 11:58" });
    expect(meetingState(m({ start: iso(-40), end: iso(20), last_ended_at: iso(-60) }), NOW).state).toBe("soon");          // пробный вход до начала — не «завершена»
    expect(meetingState(m({ cancelled: true, live: { started_at: iso(-1), participants: 1 } }), NOW).state).toBe("cancelled");
  });

  it("серия и временная комната: без ложного отсчёта", () => {
    expect(meetingState(m({ recurring: true }), NOW).state).toBe("scheduled");
    expect(meetingState(m({ kind: "temporary", end: null, opens_at: null }), NOW).state).toBe("open");
  });

  it("часовой пояс пользователя: дата и время показываются в нём, «сегодня/завтра» — по его календарю", () => {
    const late = m({ start: "2026-10-23T21:30:00Z", end: "2026-10-23T22:30:00Z" });
    expect(whenLabel(late, NOW, "UTC")).toBe("Сегодня · 21:30–22:30");
    expect(whenLabel(late, NOW, "Europe/Moscow")).toBe("Завтра · 00:30–01:30");          // в Москве это уже следующий день
    expect(whenLabel(m({ start: "2026-10-23T23:30:00Z", end: "2026-10-24T00:30:00Z" }), NOW, "UTC")).toBe("Сегодня 23:30 – Завтра 00:30");
    expect(whenLabel(m({ start: "2026-10-23T06:00:00Z", end: "2026-10-23T07:00:00Z" }), NOW, "America/New_York")).toBe("Сегодня · 02:00–03:00");
    expect(dayLabel("2026-11-05T09:00:00Z", NOW, "UTC")).toMatch(/^чт, 5 нояб?/);
    expect(dayLabel("2027-01-05T09:00:00Z", NOW, "UTC")).toContain("2027");
  });

  it("кнопка: «Войти», когда вход открыт и разрешён; иначе «Открыть встречу»; у отменённой кнопки нет", () => {
    expect(action(m({ opens_at: iso(-1), start: iso(9) }), "soon", NOW)).toEqual({ label: "Войти", primary: true });
    expect(action(m(), "scheduled", NOW)).toEqual({ label: "Открыть встречу", primary: false });
    expect(action(m({ opens_at: iso(-1), can_join: false }), "soon", NOW)?.label).toBe("Открыть встречу");
    expect(action(m({ cancelled: true }), "cancelled", NOW)).toBeNull();
  });

  it("частота перерисовки: раз в секунду только у самой границы начала", () => {
    expect(tickMs([m({ start: iso(60) })], NOW)).toBe(15_000);
    expect(tickMs([m({ start: new Date(NOW + 50_000).toISOString() })], NOW)).toBe(1000);
  });
});
