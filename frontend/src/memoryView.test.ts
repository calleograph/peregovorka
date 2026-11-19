import { describe, expect, it } from "vitest";
import type { MemoryEvent, MemoryThread, RoomMemoryState } from "./api";
import { history, inPeriod, lanes, matchThread, periodFor, summarize } from "./memoryView";

const ev = (id: string, thread: string, meeting: string, kind: MemoryEvent["kind"], at: string, text = "x", extra: Partial<MemoryEvent> = {}): MemoryEvent => ({
  id, thread, meeting, kind, text, at, speaker: null, assignee: null, deadline: null, quote: null, sec: null, segment_id: null, source_gone: false, topic_id: "t", ...extra,
});
const th = (id: string, title: string, first: string, last: string, state = "discussed"): MemoryThread => ({
  id, title, ai_title: title, summary: null, category: "general", state, state_title: state, user_state: null, author: null, author_is_user: false, note: null,
  first_at: first, last_at: last, meetings: [], counts: { decision: 0, task: 0, proposal: 0, question: 0 },
});
const ST: RoomMemoryState = {
  room: { id: "r", name: "Комната" }, enabled: true, keep: false, can_manage: true, status: "idle", error: null, updated_at: null,
  coverage: { meetings: 3, with_transcript: 3, with_map: 3, in_memory: 3, without_map: 0, partial_view: false },
  threads: [th("a", "Отчёты по почте", "2026-08-01T09:00:00Z", "2026-10-20T09:00:00Z", "reopened"), th("b", "Найм инженера", "2026-10-10T09:00:00Z", "2026-10-10T09:00:00Z", "new")],
  events: [
    ev("1", "a", "m1", "mention", "2026-08-01T09:00:00Z", "Предложили рассылку"), ev("2", "a", "m1", "proposal", "2026-08-01T09:05:00Z", "Рассылать отчёты по почте"),
    ev("3", "a", "m2", "decision", "2026-10-10T09:10:00Z", "Только с разрешения организатора"), ev("4", "b", "m2", "mention", "2026-10-10T09:30:00Z", "Нужен инженер"),
    ev("5", "a", "m3", "mention", "2026-10-20T09:00:00Z", "Снова о рассылке"), ev("6", "a", "m3", "task", "2026-10-20T09:05:00Z", "Настроить подтверждение", { assignee: "Ёлкин" }),
  ],
  meetings: { m1: { started_at: "2026-08-01T09:00:00Z", ended_at: null }, m2: { started_at: "2026-10-10T09:00:00Z", ended_at: null }, m3: { started_at: "2026-10-20T09:00:00Z", ended_at: null } },
  observations: [], dismissed: 0, states: {},
};

describe("эволюция обсуждений: данные страницы", () => {
  it("период: «квартал» отсекает старые события, новые темы считаются внутри периода", () => {
    const p = periodFor("quarter", new Date("2026-11-15T00:00:00Z"));
    expect(inPeriod("2026-08-01T09:00:00Z", p)).toBe(false);
    const s = summarize(ST, p);
    expect(s.stats).toMatchObject({ active: 2, fresh: 1, continued: 1, decisions: 1, tasks: 1, reopened: 1 });
    expect(s.meetings).toEqual(["m2", "m3"]);
    expect(summarize(ST, periodFor("all")).meetings).toEqual(["m1", "m2", "m3"]);
  });
  it("развитие тем: по встрече — самое значимое событие (решение важнее обсуждения)", () => {
    const s = summarize(ST, periodFor("all"));
    const rows = lanes(s.threads, s.events, s.meetings);
    expect(rows[0].thread.id).toBe("a");
    expect(rows[0].cells).toEqual(["proposal", "decision", "task"]);
    expect(rows[1].cells).toEqual([null, "mention", null]);
  });
  it("поиск по темам находит по тексту событий и исполнителю, учитывает фильтр состояния и «ё»", () => {
    expect(matchThread(ST.threads[0], ST.events, "разрешения", "")).toBe(true);
    expect(matchThread(ST.threads[0], ST.events, "елкин", "")).toBe(true);
    expect(matchThread(ST.threads[0], ST.events, "инженер", "")).toBe(false);
    expect(matchThread(ST.threads[0], ST.events, "", "new")).toBe(false);
  });
  it("история темы сгруппирована по встречам в порядке времени", () => {
    const h = history(ST.threads[0], ST.events);
    expect(h.map((g) => g.meeting)).toEqual(["m1", "m2", "m3"]);
    expect(h[0].topic).toBe("Предложили рассылку");
    expect(h[2].events.map((e) => e.kind)).toEqual(["mention", "task"]);
  });
});
