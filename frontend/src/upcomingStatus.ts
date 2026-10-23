import type { UpcomingMeeting } from "./api";

/** Состояние карточки «Предстоящие встречи». «Идёт» — только по фактической встрече (кто-то вошёл), не по наступлению времени начала. */
export type MeetingState = "scheduled" | "soon" | "live" | "ended" | "cancelled" | "open";
export const STATE_LABEL: Record<MeetingState, string> = {
  scheduled: "Запланирована", soon: "Скоро начнётся", live: "Идёт", ended: "Завершена", cancelled: "Отменена", open: "Открыта",
};

const SOON_MS = 15 * 60_000;            // «Скоро начнётся» — за 15 минут до начала (или раньше, если вход уже открыт)

/** Остаток времени по-русски, крупными единицами: «3 д 4 ч», «2 ч 15 мин», «18 мин», «45 сек». */
export function span(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return h ? `${d} д ${h} ч` : `${d} д`;
  if (h) return m ? `${h} ч ${m} мин` : `${h} ч`;
  if (m) return `${m} мин`;
  return `${s} сек`;
}

// Форматтеры создаются один раз на (язык, параметры, пояс): toLocale…String создаёт новый на каждый вызов — на сотнях карточек это секунды
const FMT = new Map<string, Intl.DateTimeFormat>();
const fmt = (locale: string, o: Intl.DateTimeFormatOptions, tz?: string) => {
  const key = `${locale}|${JSON.stringify(o)}|${tz ?? ""}`;
  let f = FMT.get(key);
  if (!f) { f = new Intl.DateTimeFormat(locale, tz ? { ...o, timeZone: tz } : o); FMT.set(key, f); }
  return f;
};
export const hhmm = (iso: string, tz?: string) => fmt("ru-RU", { hour: "2-digit", minute: "2-digit" }, tz).format(new Date(iso));
const ymd = (ms: number, tz?: string) => fmt("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }, tz).format(ms);        // ГГГГ-ММ-ДД в нужном поясе

/** «Сегодня», «Завтра», иначе «пт, 24 окт» (год — если не текущий) — в часовом поясе пользователя. */
export function dayLabel(iso: string, now: number, tz?: string): string {
  const t = new Date(iso).getTime();
  if (ymd(t, tz) === ymd(now, tz)) return "Сегодня";
  if (ymd(t, tz) === ymd(now + 86400_000, tz)) return "Завтра";
  if (ymd(t, tz) === ymd(now - 86400_000, tz)) return "Вчера";
  const sameYear = ymd(t, tz).slice(0, 4) === ymd(now, tz).slice(0, 4);
  return fmt("ru-RU", { weekday: "short", day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) }, tz).format(t).replace(/\.$/, "");
}

/** Дата и время встречи: «Сегодня · 15:00–15:30»; через полночь — «Сегодня 23:30 – Завтра 00:30». */
export function whenLabel(m: Pick<UpcomingMeeting, "start" | "end" | "recurring" | "kind">, now: number, tz?: string): string {
  if (m.kind !== "scheduled") return `Создана ${dayLabel(m.start, now, tz).toLowerCase()} в ${hhmm(m.start, tz)}`;
  if (m.recurring && !m.end) return `Повторяющаяся · начало в ${hhmm(m.start, tz)}`;
  const d1 = dayLabel(m.start, now, tz), pre = m.recurring ? "Серия · " : "";       // у серии — ближайшее занятие
  if (!m.end) return `${pre}${d1} · ${hhmm(m.start, tz)}`;
  const d2 = dayLabel(m.end, now, tz);
  return pre + (d1 === d2 ? `${d1} · ${hhmm(m.start, tz)}–${hhmm(m.end, tz)}` : `${d1} ${hhmm(m.start, tz)} – ${d2} ${hhmm(m.end, tz)}`);
}

/** Состояние и строка отсчёта: «Через 2 ч 15 мин», «Через 18 мин», «Начало через 45 сек», «Встреча идёт · 12 мин». */
export function meetingState(m: UpcomingMeeting, now: number, tz?: string): { state: MeetingState; text: string } {
  if (m.cancelled) return { state: "cancelled", text: "Встреча отменена организатором" };
  if (m.live) {
    const min = Math.floor((now - new Date(m.live.started_at).getTime()) / 60_000);
    return { state: "live", text: min >= 1 ? `Встреча идёт · ${span(min * 60_000)}` : "Встреча только началась" };
  }
  if (m.kind !== "scheduled") return { state: "open", text: "Комната открыта · сейчас в ней никого нет" };
  if (m.recurring && !m.end) return { state: "scheduled", text: "Серия встреч: вход открыт, время — по календарю" };
  const start = new Date(m.start).getTime();
  const ended = m.last_ended_at && new Date(m.last_ended_at).getTime() >= start && now >= start;
  if ((m.end && now >= new Date(m.end).getTime()) || ended) {
    return { state: "ended", text: `Завершилась в ${hhmm(ended ? m.last_ended_at! : m.end!, tz)}` };
  }
  if (now >= start) return { state: "soon", text: `Начало было в ${hhmm(m.start, tz)} · участники ещё не вошли` };
  const left = start - now;
  const open = !!m.opens_at && now >= new Date(m.opens_at).getTime();
  return { state: left <= SOON_MS || open ? "soon" : "scheduled", text: left < 60_000 ? `Начало через ${span(left)}` : `Через ${span(left)}` };
}

/** Как часто перерисовывать отсчёт: раз в секунду, только когда у какой-то встречи меньше двух минут до начала; иначе раз в 15 секунд. */
export function tickMs(items: UpcomingMeeting[], now: number): number {
  return items.some((m) => !m.cancelled && !m.live && m.kind === "scheduled" && !!m.end && Math.abs(new Date(m.start).getTime() - now) < 120_000) ? 1000 : 15_000;
}

/** Кнопка карточки: «Войти» — вход уже открыт и он разрешён; «Открыть встречу» — страница встречи (до открытия — обратный отсчёт); null — кнопки нет. */
export function action(m: UpcomingMeeting, state: MeetingState, now: number): { label: string; primary: boolean } | null {
  if (state === "cancelled") return null;
  if (state === "ended") return { label: "Открыть встречу", primary: false };
  const open = m.kind !== "scheduled" || m.recurring || state === "live" || !m.opens_at || now >= new Date(m.opens_at).getTime();
  return m.can_join && open ? { label: "Войти", primary: true } : { label: "Открыть встречу", primary: false };
}
