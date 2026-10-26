// Календарь Outlook в Peregovorka: чистая логика (периоды, группировка по дням, раскладка пересекающихся событий, подписи, тело запроса на создание).
// Время событий приходит в UTC и показывается в поясе браузера; неделя начинается с понедельника.

export type ViewMode = "day" | "week" | "month" | "list";
export type EventKind = "meeting" | "series" | "permanent" | "event";
export type EventStatus = "scheduled" | "live" | "ended" | "cancelled";

export type ShowAs = "free" | "tentative" | "busy" | "oof" | "workingElsewhere";
export type EventShape = "single" | "series" | "occurrence" | "exception";

export interface CalEvent {
  id: string; source: "exchange" | "peregovorka"; title: string; start: string; end: string; all_day: boolean;
  kind: EventKind; status: EventStatus; recurring: boolean; location: string | null; etag: string | null;
  room_id: string | null; mine: boolean; join_url: string | null; guest_url: string | null; editable: boolean;
  show_as?: ShowAs; meeting?: boolean; calendar_id?: string; organizer?: string | null; series_id?: string | null; event_kind?: EventShape; can_edit?: boolean;
}

/** Цвет карточки — по главному признаку; остальное (конференция Peregovorka, повторение, отмена) не цветом, а меткой и значком.
 *  Серый — отменённое; сиреневый — повторяющееся; голубой — разовая встреча (с участниками или конференцией Peregovorka); светло-жёлтый — обычное событие Outlook. */
export type Tone = "meeting" | "series" | "plain" | "cancelled";
export const TONES: Record<Tone, { label: string; cls: string }> = {
  meeting: { label: "Разовая встреча", cls: "t-meeting" }, series: { label: "Повторяющаяся", cls: "t-series" },
  plain: { label: "Событие Outlook", cls: "t-plain" }, cancelled: { label: "Отменено", cls: "t-cancelled" },
};
/** Конференция Peregovorka: к событию привязана комната (разовая, серия) или постоянная переговорка. Это зелёная метка поверх любого цвета. */
export const isConference = (ev: CalEvent): boolean => ev.kind !== "event" || !!ev.room_id;
export function toneOf(ev: CalEvent): Tone {
  if (ev.status === "cancelled") return "cancelled";
  if (ev.recurring) return "series";
  return isConference(ev) || ev.meeting ? "meeting" : "plain";
}
export const SHOW_AS: Record<ShowAs, string> = { free: "Свободен", tentative: "Под вопросом", busy: "Занят", oof: "Нет на месте", workingElsewhere: "Работаю в другом месте" };

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
export interface HourWindow { from: number; to: number }          // показываемые часы суток: [from, to), 0 ≤ from < to ≤ 24
export const FULL_DAY: HourWindow = { from: 0, to: 24 };

/** Раскладка суток по часам: top и height — доли показываемого окна (0..1); пересекающиеся события делят ширину на колонки. Событие короче 20 минут рисуется не тоньше 20 минут. */
export function layoutDay(slices: Slice[], day: Date, win: HourWindow = FULL_DAY): Placed[] {
  const DAY = (win.to - win.from) * 3600_000;
  const MIN = 20 * 60_000;
  // положение — по «настенным» часам дня, а не по разности миллисекунд: в сутки перехода на летнее/зимнее время (23 или 25 часов) событие остаётся на своей отметке часов
  const wall = (t: Date): number => (sameDay(t, day) ? (t.getHours() * 60 + t.getMinutes()) * 60_000 : 24 * 3600_000) - win.from * 3600_000;
  const items = slices.filter((s) => !s.ev.all_day).map((slice) => {
    const a = wall(slice.start), b = Math.max(wall(slice.end), a + MIN);
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
  return items.map((i) => {
    const a = Math.max(0, i.a), b = Math.min(DAY, i.b);              // окно уже расширено до всех событий; обрезка — страховка
    return { slice: i.slice, top: a / DAY, height: Math.max(0, Math.min(1 - a / DAY, (b - a) / DAY)), col: i.col, cols: i.cols };
  });
}

// ---------------------------------------------------------------------------------------------- рабочие часы и окно сетки
export interface WorkHours { from: number; to: number; onlyWork: boolean }
export const DEFAULT_WORK: WorkHours = { from: 8, to: 19, onlyWork: false };

/** Нормализация настройки: целые часы, начало < конца, не короче двух часов. */
export function cleanWork(w: Partial<WorkHours> | null | undefined): WorkHours {
  const from = Math.min(22, Math.max(0, Math.floor(Number(w?.from ?? DEFAULT_WORK.from)) || 0));
  let to = Math.min(24, Math.max(1, Math.floor(Number(w?.to ?? DEFAULT_WORK.to)) || 0));
  if (to < from + 2) to = Math.min(24, from + 2);
  return { from, to, onlyWork: !!w?.onlyWork };
}

/** Какие часы показывать. Без «только рабочие» — все сутки. С ним — рабочий диапазон, расширенный до самых ранних и поздних событий периода: ни одно событие не скрыто. */
export function gridWindow(byDay: Map<string, Slice[]>, work: WorkHours): HourWindow {
  if (!work.onlyWork) return FULL_DAY;
  let lo = work.from, hi = work.to;
  for (const list of byDay.values()) {
    for (const s of list) {
      if (s.ev.all_day) continue;
      lo = Math.min(lo, s.start.getHours());                       // по настенным часам: сутки перехода на другое время не сдвигают границы
      const e = s.end, endsAtMidnight = e.getHours() === 0 && e.getMinutes() === 0 && !sameDay(e, s.start);
      hi = Math.max(hi, endsAtMidnight ? 24 : Math.min(24, e.getHours() + (e.getMinutes() > 0 ? 1 : 0)));
    }
  }
  return { from: Math.max(0, lo), to: Math.min(24, Math.max(hi, lo + 2)) };
}

const SNAP_MIN = 15;
/** Время по положению указателя в колонке дня: y — доля высоты (0..1) показываемого окна, округление до 15 минут. */
export function timeAt(day: Date, y: number, win: HourWindow = FULL_DAY): Date {
  const minutes = win.from * 60 + Math.min(1, Math.max(0, y)) * (win.to - win.from) * 60;
  const snapped = Math.round(minutes / SNAP_MIN) * SNAP_MIN;
  const d = startOfDay(day);
  d.setMinutes(Math.min(snapped, 24 * 60 - SNAP_MIN));
  return d;
}

/** Выделение мышью: два момента в любом порядке → начало и длительность в минутах (не меньше 15). Один щелчок без протяжки — получасовой интервал. */
export function selectionOf(a: Date, b: Date, click = false): { start: Date; minutes: number } {
  const [s, e] = a <= b ? [a, b] : [b, a];
  const minutes = click ? 30 : Math.max(SNAP_MIN, Math.round((e.getTime() - s.getTime()) / 60000));
  return { start: s, minutes: Math.min(minutes, 24 * 60) };
}

export const durationText = (min: number): string => min < 60 ? `${min} мин` : min % 60 === 0 ? `${min / 60} ч` : `${Math.floor(min / 60)} ч ${min % 60} мин`;

/** Короткая сводка события для подписи и экранного диктора: «Тип. Название. 10:00–10:45. Статус». */
export function describe(ev: CalEvent): string {
  const when = ev.all_day ? "весь день" : `${timeLabel(ev.start)}–${timeLabel(ev.end)}`;
  return [TONES[toneOf(ev)].label, isConference(ev) ? "конференция Peregovorka" : "", ev.title, when, ev.show_as && ev.show_as !== "busy" ? SHOW_AS[ev.show_as] : "", STATUSES[ev.status].label].filter(Boolean).join(". ");
}

// ---------------------------------------------------------------------------------------------- создание встречи
export interface CreateForm {
  title: string; date: string; time: string; minutes: number;
  repeat: "none" | "daily" | "weekly" | "monthly"; days: number[]; endMode: "never" | "count" | "date"; count: number; until: string;
  location: string; description: string;
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

export function defaultForm(at: Date = nextSlot(new Date()), guestsAllowed = false, minutes = 30): CreateForm {
  return { title: "", date: dateInput(at), time: timeInput(at), minutes, repeat: "none", days: [(at.getDay() + 6) % 7], endMode: "never", count: 10, until: "",
           location: "", description: "", conference: true, guest: false && guestsAllowed, attendees: [] };
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
    ...(f.location.trim() ? { location: f.location.trim() } : {}), ...(f.description.trim() ? { description: f.description.trim() } : {}),
    attendees: attendeeBody(f.attendees),
  };
  if (f.repeat !== "none") {
    body.recurrence = { type: f.repeat, interval: 1, ...(f.repeat === "weekly" ? { days: [...f.days].sort() } : {}), ...(f.endMode === "count" ? { count: f.count } : f.endMode === "date" ? { end: f.until } : {}) };
  }
  return body;
}

export type AttendeeRow = CreateForm["attendees"][number];
export const personKey = (a: AttendeeRow): string => a.id ?? (a.ref ? `ref:${a.ref}` : `mail:${a.email}`);
/** Участники для сервера: сотрудник — по идентификатору (адрес берёт сервер из профиля), внешний — по адресу. */
export const attendeeBody = (list: AttendeeRow[]) => list.map((a) => (a.id || a.ref ? { id: a.id, ref: a.ref, login: a.login, optional: !!a.optional } : { email: a.email, name: a.name, optional: !!a.optional }));

const KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
/** Ключ запроса: один на открытие формы, чтобы повторное нажатие «Создать» не создавало второй раз. */
export function newKey(rand: () => number = Math.random): string {
  let s = "cal";
  for (let i = 0; i < 24; i++) s += KEY_ALPHABET[Math.floor(rand() * KEY_ALPHABET.length)];
  return s;
}

export const localZone = (): string => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };
