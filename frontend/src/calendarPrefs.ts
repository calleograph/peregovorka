// Состояние календаря, которое помнится между переходами «Календарь ↔ Переговорки ↔ Чаты» и перезагрузками страницы:
// вид и выбранная дата (на время сеанса браузера), включённые календари и рабочие часы (постоянно, на этом компьютере).
import { cleanWork, DEFAULT_WORK, MODES, type ViewMode, type WorkHours } from "./calendarView";

const K_MODE = "pg:calendarMode", K_DAY = "pg:calendarDay", K_CALS = "pg:calendarCals", K_WORK = "pg:calendarWork";

function read(store: "local" | "session", key: string): string | null {
  try { return (store === "local" ? localStorage : sessionStorage).getItem(key); } catch { return null; }
}
function write(store: "local" | "session", key: string, value: string): void {
  try { (store === "local" ? localStorage : sessionStorage).setItem(key, value); } catch { /* запоминание необязательно: браузер может его запрещать */ }
}

export interface CalendarSession { mode: ViewMode; anchor: Date; calendars: string[]; work: WorkHours }

export function parseDay(v: string | null): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v ?? "");
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12);
  return Number.isNaN(d.getTime()) || d.getMonth() !== Number(m[2]) - 1 ? null : d;
}

export function loadSession(): CalendarSession {
  const mode = read("local", K_MODE);
  let cals: string[] = [];
  try { const j = JSON.parse(read("local", K_CALS) || "[]"); if (Array.isArray(j)) cals = j.filter((x) => typeof x === "string").slice(0, 10); } catch { /* повреждённое значение — как будто его нет */ }
  let work: WorkHours = DEFAULT_WORK;
  try { work = cleanWork(JSON.parse(read("local", K_WORK) || "null")); } catch { /* значения по умолчанию */ }
  return { mode: MODES.some((m) => m.id === mode) ? (mode as ViewMode) : "week", anchor: parseDay(read("session", K_DAY)) ?? new Date(), calendars: cals.length ? cals : ["primary"], work };
}

const pad = (n: number) => String(n).padStart(2, "0");
export const saveMode = (m: ViewMode): void => write("local", K_MODE, m);
export const saveDay = (d: Date): void => write("session", K_DAY, `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
export const saveCalendars = (ids: string[]): void => write("local", K_CALS, JSON.stringify(ids));
export const saveWork = (w: WorkHours): void => write("local", K_WORK, JSON.stringify(w));
