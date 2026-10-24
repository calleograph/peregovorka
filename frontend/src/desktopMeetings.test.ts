import { describe, expect, it } from "vitest";
import type { Meeting, UpcomingMeeting } from "./api";
import { diffProtocols, diffUpcoming } from "./desktopMeetings";

const NOW = Date.parse("2026-10-24T10:00:00Z");
const m = (over: Partial<UpcomingMeeting>): UpcomingMeeting => ({ id: "a", slug: "a", name: "Планёрка", kind: "scheduled", start: "2026-10-24T12:00:00Z", end: null, opens_at: null, recurring: false,
  organizer: "Анна", cancelled: false, live: null, last_ended_at: null, invitees: { count: 2, names: null, can_list: false }, mine: true, can_join: true, can_manage: false,
  has_password: false, url: "/m/abcd1234", guest_token: null, sync: null, ...over });

describe("уведомления о встречах в приложении", () => {
  it("первый опрос ничего не сообщает, кроме «скоро начало»", () => {
    expect(diffUpcoming(null, [m({})], NOW).notes).toEqual([]);
    const soon = diffUpcoming(null, [m({ start: "2026-10-24T10:07:00Z" })], NOW).notes;
    expect(soon.map((n) => [n.tag, n.title, n.body, n.route])).toEqual([["meeting-soon:a:2026-10-24T10:07:00Z", "Скоро начало: Планёрка", "Через 7 мин", "/m/abcd1234"]]);
  });
  it("новая встреча, перенос и отмена — по сравнению со снимком; чужие и равноправные комнаты не сообщаются", () => {
    const base = diffUpcoming(null, [m({})], NOW).next;
    expect(diffUpcoming(base, [m({}), m({ id: "b", name: "Ретро" })], NOW).notes.map((n) => n.tag)).toEqual(["meeting-new:b"]);
    expect(diffUpcoming(base, [m({ start: "2026-10-24T13:00:00Z" })], NOW).notes[0].title).toBe("Встреча перенесена: Планёрка");
    expect(diffUpcoming(base, [m({ cancelled: true })], NOW).notes.map((n) => n.title)).toEqual(["Встреча отменена: Планёрка"]);
    expect(diffUpcoming(base, [m({}), m({ id: "c", mine: false }), m({ id: "d", kind: "peer" })], NOW).notes).toEqual([]);
    expect(diffUpcoming(base, [m({ start: "2026-10-24T12:00:30Z" })], NOW).notes, "сдвиг меньше минуты — не перенос").toEqual([]);
  });
  it("идущая и отменённая встреча не дают «скоро начало»", () => {
    expect(diffUpcoming(null, [m({ start: "2026-10-24T10:05:00Z", live: { started_at: "x", participants: 1 } })], NOW).notes).toEqual([]);
    expect(diffUpcoming(null, [m({ start: "2026-10-24T10:05:00Z", cancelled: true })], NOW).notes).toEqual([]);
  });
  it("протокол готов: только переход 0 → есть и только недавние встречи", () => {
    const mt = (o: Partial<Meeting>): Meeting => ({ id: "x", room_id: "r", room_name: "Продажи", started_at: "2026-10-24T08:00:00Z", ended_at: "2026-10-24T09:00:00Z", end_reason: null,
      transcription_enabled: true, participants: [], segments: 1, recordings: 0, protocols: 0, ...o });
    const base = diffProtocols(null, [mt({})], NOW);
    expect(base.notes).toEqual([]);
    expect(diffProtocols(base.next, [mt({ protocols: 1 })], NOW).notes.map((n) => [n.title, n.route])).toEqual([["Протокол готов: Продажи", "/history/x"]]);
    expect(diffProtocols({ x: 1 }, [mt({ protocols: 2 })], NOW).notes).toEqual([]);
    expect(diffProtocols({}, [mt({ protocols: 1, ended_at: "2026-10-01T09:00:00Z" })], NOW).notes).toEqual([]);
  });
});
