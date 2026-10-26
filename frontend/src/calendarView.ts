// Календарь Outlook в Peregovorka: чистая логика (периоды, группировка по дням, раскладка пересекающихся событий, подписи, тело запроса на создание).
// Время событий приходит в UTC и показывается в поясе браузера; неделя начинается с понедельника.

export type ViewMode = "day" | "week" | "month" | "list";
export type EventKind = "meeting" | "series" | "permanent" | "event";
export type EventStatus = "scheduled" | "live" | "ended" | "cancelled";

export interface CalEvent {
  id: string; source: "exchange" | "peregovorka"; title: string; start: string; end: string; all_day: boolean;
  kind: EventKind; status: EventStatus; recurring: boolean; location: string | null; etag: string | null;
  room_id: string | null; mine: boolean; join_url: string | null; guest_url: string | null; editable: boolean;
}

export const MODES: { id: ViewMode; label: string }[] = [{ id: "day", label: "День" }, { id: "week", label: "Неделя" }, { id: "month", label: "Месяц" }, { id: "list", label: "Список" }];

/** Тип события: подпись (не только цвет), пиктограмма и класс оформления. */
export const KINDS: Record<EventKind, { label: string; icon: "video" | "calendar" | "users" | "grid" | "retry"; cls: string; hint: string }> = {
  meeting: { label: "Встреча", icon: "video", cls: "k-meeting", hint: "Разовая встреча Peregovorka" },
  series: { label: "Серия", icon: "retry", cls: "k-series", hint: "Повторяющаяся встреча Peregovorka" },
  permanent: { label: "Постоянная комната", icon: "grid", cls: "k-permanent", hint: "Встреча в постоянной переговорке" },
  event: { label: "Событие Outlook", icon: "calendar", cls: "k-event", hint: "Обычное событие календаря без конференции" },
};
export const STATUSES: Record<EventStatus, { label: string; cls: string }> = {
  scheduled: { label: "Запланирована", cls: "s-scheduled" }, live: { label: "Идёт", cls: "s-live" }, ended: { label: "Завершена", cls: "s-ended" }, cancelled: { label: "Отменена", cls: "s-cancelled" },
};

// ---------------------------------------------------------------------------------------------- даты
export const startOfDay = (d: Date): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const addDays = (d: Date, n: number): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes(), d.getSeconds());
export const startOfWeek = (d: Date): Date => addDays(startOfDay(d), -((d.getDay() + 6) % 7));
export const sameDay = (a: Date, b: Date): boolean => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
export const dayKey = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Границы запроса к серверу: сутки, неделя, сетка месяца (с хвостами соседних недель), список — ближайшие 30 дней. Верхняя граница не входит. */
export function rangeFor(mode: ViewMode, anchor: Date): { from: Date; to: Date } {
  const day = startOfDay(anchor);
  if (mode === "day") return { from: day, to: addDays(day, 1) };
  if (mode === "week") { const w = startOfWeek(day); return { from: w, to: addDays(w, 7) }; }
  if (mode === "month") {
    const first = startOfWeek(new Date(day.getFullYear(), day.getMonth(), 1));
    const last = new Date(day.getFullYear(), day.getMonth() + 1, 0);
    const end = addDays(startOfWeek(last), 7);
    return { from: first, to: end };
  }
  return { from: day, to: addDays(day, 30) };
}

export function shift(mode: ViewMode, anchor: Date, dir: -1 | 1): Date {
  if (mode === "day") return addDays(anchor, dir);
  if (mode === "week") return addDays(anchor, 7 * dir);
  if (mode === "month") return new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1);
  return addDays(anchor, 30 * dir);
}

const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
export const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const WEEKDAYS_FULL = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];

export function title(mode: ViewMode, anchor: Date): string {
  const { from, to } = rangeFor(mode, anchor);
  if (mode === "month") return `${MONTHS[anchor.getMonth()][0].toUpperCase()}${MONTHS[anchor.getMonth()].slice(1)} ${anchor.getFullYear()}`;
  if (mode === "day") return `${WEEKDAYS_FULL[anchor.getDay()]}, ${anchor.getDate()} ${MONTHS_GEN[anchor.getMonth()]} ${anchor.getFullYear()}`;
  const last = addDays(to, -1);
  if (from.getMonth() === last.getMonth()) return `${from.getDate()}–${last.getDate()} ${MONTHS_GEN[last.getMonth()]} ${last.getFullYear()}`;
  return `${from.getDate()} ${MONTHS_GEN[from.getMonth()]} – ${last.getDate()} ${MONTHS_GEN[last.getMonth()]} ${last.getFullYear()}`;
}

export const dayLabel = (d: Date): string => `${WEEKDAYS_FULL[d.getDay()]}, ${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
export const timeLabel = (iso: string): string => new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

/** Дни сетки между границами: массив дат. */
export function daysOf(from: Date, to: Date): Date[] {
  const out: Date[] = [];
  for (let d = startOfDay(from); d < to; d = addDays(d, 1)) out.push(d);
  return out;
}

// ---------------------------------------------------------------------------------------------- события по дням
export interface Slice { ev: CalEvent; start: Date; end: Date; continued: boolean; continues: boolean }

/** События по дням: событие, идущее через полночь, попадает в каждый затронутый день своим куском. Внутри дня — по началу, затем по названию. */
export function groupByDay(events: CalEvent[], from: Date, to: Date): Map<string, Slice[]> {
  const map = new Map<string, Slice[]>();
  for (const d of daysOf(from, to)) map.set(dayKey(d), []);
  for (const ev of events) {
    const s = new Date(ev.start);
    let e = new Date(ev.end);
    if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) continue;
    if (e < s) e = s;
    for (let d = startOfDay(s); d < e || sameDay(d, s); d = addDays(d, 1)) {
      const bucket = map.get(dayKey(d));
      if (bucket) {
        const dayEnd = addDays(d, 1);
        bucket.push({ ev, start: s > d ? s : d, end: e < dayEnd ? e : dayEnd, continued: s < d, continues: e > dayEnd });
      }
      if (e <= addDays(d, 1)) break;
    }
  }
  for (const list of map.values()) list.sort((a, b) => a.start.getTime() - b.start.getTime() || a.ev.title.localeCompare(b.ev.title, "ru"));
  return map;
}

export interface Placed { slice: Slice; top: number; height: number; col: number; cols: number }

/** Раскладка суток по часам: top и height — доли суток (0..1); пересекающиеся события делят ширину на колонки. Событие короче 20 минут рисуется не тоньше 20 минут. */
export function layoutDay(slices: Slice[], day: Date): Placed[] {
  const d0 = startOfDay(day).getTime();
  const DAY = 24 * 3600_000;
  const MIN = 20 * 60_000;
  const items = slices.filter((s) => !s.ev.all_day).map((slice) => {
    const a = slice.start.getTime() - d0, b = Math.max(slice.end.getTime() - d0, a + MIN);
    return { slice, a, b, col: 0, cols: 1 };
  }).sort((x, y) => x.a - y.a || y.b - x.b);
  // группы связанных пересечений: каждая делит ширину поровну
  let group: typeof items = [], groupEnd = -1;
  const flush = () => {
    const colEnds: number[] = [];
    for (const it of group) {
      let c = colEnds.findIndex((end) => end <= it.a);
      if (c < 0) { c = colEnds.length; colEnds.push(it.b); } else colEnds[c] = it.b;
      it.col = c;
    }
    for (const it of group) it.cols = colEnds.length;
    group = [];
  };
  for (const it of items) {
    if (group.length && it.a >= groupEnd) { flush(); groupEnd = -1; }
    group.push(it);
    groupEnd = Math.max(groupEnd, it.b);
  }
  if (group.length) flush();
  return items.map((i) => ({ slice: i.slice, top: i.a / DAY, height: Math.min(1 - i.a / DAY, (i.b - i.a) / DAY), col: i.col, cols: i.cols }));
}

export const durationText = (min: number): string => min < 60 ? `${min} мин` : min % 60 === 0 ? `${min / 60} ч` : `${Math.floor(min / 60)} ч ${min % 60} мин`;

/** Короткая сводка события для подписи и экранного диктора: «Тип. Название. 10:00–10:45. Статус». */
export function describe(ev: CalEvent): string {
  const when = ev.all_day ? "весь день" : `${timeLabel(ev.start)}–${timeLabel(ev.end)}`;
  return [KINDS[ev.kind].label, ev.title, when, ev.recurring ? "повторяется" : "", STATUSES[ev.status].label].filter(Boolean).join(". ");
}

// ---------------------------------------------------------------------------------------------- создание встречи
export interface CreateForm {
  title: string; date: string; time: string; minutes: number;
  repeat: "none" | "daily" | "weekly" | "monthly"; days: number[]; endMode: "never" | "count" | "date"; count: number; until: string;
  conference: boolean; guest: boolean; attendees: { id: string | null; ref: string | null; login: string | null; name: string; email?: string; optional?: boolean }[];
}

export const DURATIONS = [15, 30, 45, 60, 90, 120, 180];
export const REPEATS: { id: CreateForm["repeat"]; label: string }[] = [{ id: "none", label: "Не повторяется" }, { id: "daily", label: "Каждый день" }, { id: "weekly", label: "Каждую неделю" }, { id: "monthly", label: "Каждый месяц" }];

export const pad = (n: number): string => String(n).padStart(2, "0");
export const dateInput = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const timeInput = (d: Date): string => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** Ближайшее «круглое» время начала: следующие полчаса. */
export function nextSlot(now: Date): Date {
  const d = new Date(now.getTime());
  d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60);
  return d;
}

export function defaultForm(at: Date = nextSlot(new Date()), guestsAllowed = false): CreateForm {
  return { title: "", date: dateInput(at), time: timeInput(at), minutes: 30, repeat: "none", days: [(at.getDay() + 6) % 7], endMode: "never", count: 10, until: "",
           conference: true, guest: false && guestsAllowed, attendees: [] };
}

/** Проверка формы до отправки: текст первой ошибки или null. */
export function formError(f: CreateForm, now: Date = new Date()): string | null {
  if (!f.title.trim()) return "Укажите название встречи.";
  const s = new Date(`${f.date}T${f.time}`);
  if (Number.isNaN(s.getTime())) return "Укажите дату и время начала.";
  if (s.getTime() + f.minutes * 60_000 < now.getTime() - 3600_000) return "Встреча уже закончилась: выберите будущее время.";
  if (f.repeat === "weekly" && !f.days.length) return "Выберите хотя бы один день недели.";
  if (f.repeat !== "none" && f.endMode === "date" && (!f.until || f.until < f.date)) return "Дата окончания повторения должна быть не раньше первой встречи.";
  if (f.repeat !== "none" && f.endMode === "count" && !(f.count >= 1 && f.count <= 999)) return "Число повторений — от 1 до 999.";
  return null;
}

/** Тело запроса `POST /calendar/events`: время — в UTC, пояс браузера передаётся отдельно (серия не «плывёт» при смене сезонного времени). */
export function buildCreateBody(f: CreateForm, key: string, tz: string): Record<string, unknown> {
  const s = new Date(`${f.date}T${f.time}`);
  const e = new Date(s.getTime() + f.minutes * 60_000);
  const body: Record<string, unknown> = {
    key, subject: f.title.trim(), start: s.toISOString(), end: e.toISOString(), tz, conference: f.conference, guest_link: f.conference && f.guest,
    attendees: f.attendees.map((a) => (a.id || a.ref ? { id: a.id, ref: a.ref, login: a.login, optional: !!a.optional } : { email: a.email, name: a.name, optional: !!a.optional })),
  };
  if (f.repeat !== "none") {
    body.recurrence = { type: f.repeat, interval: 1, ...(f.repeat === "weekly" ? { days: [...f.days].sort() } : {}), ...(f.endMode === "count" ? { count: f.count } : f.endMode === "date" ? { end: f.until } : {}) };
  }
  return body;
}

const KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
/** Ключ запроса: один на открытие формы, чтобы повторное нажатие «Создать» не создавало второй раз. */
export function newKey(rand: () => number = Math.random): string {
  let s = "cal";
  for (let i = 0; i < 24; i++) s += KEY_ALPHABET[Math.floor(rand() * KEY_ALPHABET.length)];
  return s;
}

export const localZone = (): string => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };
