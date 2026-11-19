import type { MemoryEvent, MemoryKind, MemoryThread, RoomMemoryState } from "./api";

/** Период просмотра памяти комнаты: от и до (включительно); null — без границы. */
export interface Period { from: string | null; to: string | null }
export const PERIODS: [string, string, number | null][] = [["month", "Месяц", 31], ["quarter", "Квартал", 92], ["year", "Год", 366], ["all", "Всё время", null]];

export function periodFor(id: string, now = new Date()): Period {
  const days = PERIODS.find((p) => p[0] === id)?.[2];
  if (!days) return { from: null, to: null };
  return { from: new Date(now.getTime() - days * 86400000).toISOString().slice(0, 10), to: null };
}

export const inPeriod = (iso: string, p: Period) => (!p.from || iso.slice(0, 10) >= p.from) && (!p.to || iso.slice(0, 10) <= p.to);

export const KIND_LABEL: Record<MemoryKind, string> = { mention: "Обсуждали", proposal: "Предложение", decision: "Решение", task: "Поручение", question: "Открытый вопрос" };

/** События по темам; темы, у которых в периоде что-то было, — «активные». Новая в периоде — первое упоминание внутри периода. */
export function summarize(st: RoomMemoryState, p: Period) {
  const ev = st.events.filter((e) => inPeriod(e.at, p));
  const active = new Set(ev.map((e) => e.thread));
  const threads = st.threads.filter((t) => active.has(t.id));
  const fresh = threads.filter((t) => inPeriod(t.first_at, p));
  const count = (k: MemoryKind) => ev.filter((e) => e.kind === k).length;
  const meetings = [...new Set(ev.map((e) => e.meeting))].sort((a, b) => (st.meetings[a]?.started_at ?? "").localeCompare(st.meetings[b]?.started_at ?? ""));
  return {
    events: ev, threads, meetings,
    stats: { active: threads.length, fresh: fresh.length, continued: threads.length - fresh.length, decisions: count("decision"), tasks: count("task"),
             questions: count("question"), attention: st.observations.filter((o) => o.tone === "attention" && inPeriod(o.at, p)).length,
             reopened: threads.filter((t) => t.state === "reopened").length, dormant: threads.filter((t) => t.state === "dormant").length },
  };
}

/** Строки «развития тем»: по теме — какие события на каких встречах периода (решение важнее поручения, поручение важнее вопроса и т. д.). */
const RANK: MemoryKind[] = ["decision", "task", "question", "proposal", "mention"];
export function lanes(threads: MemoryThread[], events: MemoryEvent[], meetings: string[], limit = 14) {
  const byThread = new Map<string, MemoryEvent[]>();
  for (const e of events) byThread.set(e.thread, [...(byThread.get(e.thread) ?? []), e]);
  return threads
    .map((t) => {
      const evs = byThread.get(t.id) ?? [];
      const cells = meetings.map((m) => {
        const here = evs.filter((e) => e.meeting === m);
        return here.length ? RANK.find((k) => here.some((e) => e.kind === k)) ?? "mention" : null;
      });
      return { thread: t, cells, weight: cells.filter(Boolean).length * 2 + evs.filter((e) => e.kind !== "mention").length };
    })
    .sort((a, b) => b.weight - a.weight || b.thread.last_at.localeCompare(a.thread.last_at))
    .slice(0, limit);
}

/** Поиск по темам: название, суть, заметка, автор и формулировки событий (без учёта регистра и «ё»). */
const norm = (s: string | null | undefined) => (s ?? "").toLowerCase().replace(/ё/g, "е");
export function matchThread(t: MemoryThread, events: MemoryEvent[], q: string, state: string): boolean {
  if (state && t.state !== state) return false;
  const words = norm(q).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = norm([t.title, t.ai_title, t.summary, t.note, t.author, ...events.filter((e) => e.thread === t.id).map((e) => `${e.text} ${e.assignee ?? ""}`)].join(" "));
  return words.every((w) => hay.includes(w));
}

/** История темы: события, сгруппированные по встречам (по времени). */
export function history(t: MemoryThread, events: MemoryEvent[]) {
  const groups: { meeting: string; at: string; topic: string; events: MemoryEvent[] }[] = [];
  for (const e of events.filter((x) => x.thread === t.id).sort((a, b) => a.at.localeCompare(b.at))) {
    const g = groups.find((x) => x.meeting === e.meeting);
    if (g) g.events.push(e); else groups.push({ meeting: e.meeting, at: e.at, topic: "", events: [e] });
  }
  for (const g of groups) g.topic = g.events[0] ? (g.events.find((e) => e.kind === "mention") ?? g.events[0]).text : "";
  return groups;
}

export function topicIdOf(events: MemoryEvent[], threadId: string, meetingId: string): string | null {
  return events.find((e) => e.thread === threadId && e.meeting === meetingId)?.topic_id ?? null;
}

export const day = (iso: string) => new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
