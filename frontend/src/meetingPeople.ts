import type { Participant } from "./api";

/** Сколько участников показывать в таблице истории: остальные сворачиваются в «+ N». Строка истории не должна расти вместе со встречей. */
export const PEOPLE_SHOWN = 4;

const RANK: Record<string, number> = { organizer: 0, leader: 1 };

/** Порядок показа: организатор, руководители, затем остальные в порядке входа (сотрудники раньше гостей и телефонов). */
export function orderParticipants(list: Participant[]): Participant[] {
  const kind = (p: Participant) => (p.participant_type === "guest" || p.participant_type === "phone" ? 1 : 0);
  return list.map((p, i) => ({ p, i }))
    .sort((a, b) => (RANK[a.p.role ?? ""] ?? 2) - (RANK[b.p.role ?? ""] ?? 2) || kind(a.p) - kind(b.p) || a.i - b.i)
    .map((x) => x.p);
}

export interface PeopleSummary { shown: Participant[]; more: number; total: number }

export function summarizeParticipants(list: Participant[], max: number = PEOPLE_SHOWN): PeopleSummary {
  const ordered = orderParticipants(list);
  // «+ 1 участник» вместо одной строки ради одной строки экономии не нужен: если скрывается ровно один, показываем его
  const cut = ordered.length - max === 1 ? max + 1 : max;
  return { shown: ordered.slice(0, cut), more: Math.max(0, ordered.length - cut), total: ordered.length };
}

/** «+ 27 участников», «+ 2 участника», «+ 21 участник» — склонение по числу. */
export function moreLabel(n: number): string {
  const m10 = n % 10, m100 = n % 100;
  const word = m10 === 1 && m100 !== 11 ? "участник" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "участника" : "участников";
  return `+ ${n} ${word}`;
}
