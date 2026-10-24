// Приложение Windows: уведомления о встречах без новых серверных событий — сравнение снимков «Предстоящих встреч» и «Истории».
// Скоро начало, новая встреча в календаре, перенос, отмена; готов протокол прошедшей встречи. В браузере не запускается.
// Первый опрос после входа только запоминает состояние (никакой лавины уведомлений); снимок хранится в странице организации (localStorage её адреса).
import { api, type Meeting, type UpcomingMeeting } from "./api";
import { desktop, desktopNotify } from "./desktop";

export type Note = { tag: string; title: string; body: string; route: string };
export type Snap = { up: Record<string, { start: string; cancelled: boolean; name: string }>; proto: Record<string, number>; at: number };

const SOON_MIN = 10;
const time = (iso: string) => new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
const routeOf = (m: UpcomingMeeting) => (m.url && m.url.startsWith("/") ? m.url : `/rooms/${m.slug}`);

/** Что сообщить по новому списку «Предстоящих встреч» относительно прежнего снимка (prev = null — первый опрос: только «скоро начало»). */
export function diffUpcoming(prev: Snap["up"] | null, items: UpcomingMeeting[], now: number): { notes: Note[]; next: Snap["up"] } {
  const notes: Note[] = [];
  const next: Snap["up"] = {};
  for (const m of items) {
    if (!m.mine || m.kind === "peer") continue;
    next[m.id] = { start: m.start, cancelled: m.cancelled, name: m.name };
    const was = prev?.[m.id];
    const startMs = Date.parse(m.start);
    if (prev && !was && m.kind === "scheduled" && !m.cancelled && startMs > now) {
      notes.push({ tag: `meeting-new:${m.id}`, title: `Новая встреча: ${m.name}`, body: `${time(m.start)}${m.organizer ? ` · ${m.organizer}` : ""}`, route: routeOf(m) });
    } else if (was && !was.cancelled && m.cancelled) {
      notes.push({ tag: `meeting-cancel:${m.id}:${m.start}`, title: `Встреча отменена: ${m.name}`, body: time(m.start), route: "/" });
    } else if (was && !m.cancelled && Math.abs(Date.parse(was.start) - startMs) >= 60_000) {
      notes.push({ tag: `meeting-move:${m.id}:${m.start}`, title: `Встреча перенесена: ${m.name}`, body: `Новое время: ${time(m.start)}`, route: routeOf(m) });
    }
    const left = (startMs - now) / 60_000;
    if (!m.cancelled && !m.live && left > 0 && left <= SOON_MIN) {
      notes.push({ tag: `meeting-soon:${m.id}:${m.start}`, title: `Скоро начало: ${m.name}`, body: `Через ${Math.max(1, Math.round(left))} мин`, route: routeOf(m) });
    }
  }
  return { notes, next };
}

/** Протоколы прошедших встреч: было 0 → стало больше — «Протокол готов» (только недавние встречи, prev = null — первый опрос). */
export function diffProtocols(prev: Snap["proto"] | null, meetings: Meeting[], now: number): { notes: Note[]; next: Snap["proto"] } {
  const notes: Note[] = [];
  const next: Snap["proto"] = {};
  for (const m of meetings) {
    if (!m.ended_at || now - Date.parse(m.ended_at) > 3 * 86400_000) continue;
    next[m.id] = m.protocols;
    if (prev && (prev[m.id] ?? 0) === 0 && m.protocols > 0) {
      notes.push({ tag: `protocol:${m.id}`, title: `Протокол готов: ${m.room_name}`, body: `Встреча ${time(m.started_at)}`, route: `/history/${m.id}` });
    }
  }
  return { notes, next };
}

const KEY = "pg:desktop:meetings";
function load(): Snap | null {
  try { const v = JSON.parse(localStorage.getItem(KEY) || "null"); return v && typeof v === "object" && v.up ? v : null; } catch { return null; }
}
function save(s: Snap): void { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* не запомнится — повторов всё равно не будет: приложение отсекает метки */ } }

/** Запуск наблюдения (только в приложении). Возвращает остановку. */
export function watchMeetings(intervalMs = 120_000): () => void {
  if (!desktop) return () => undefined;
  let snap = load();
  let stopped = false;
  let tick = 0;
  const run = async () => {
    if (stopped) return;
    const now = Date.now();
    const fresh = !snap || now - snap.at > 24 * 3600_000;          // давно не запускалось — новых «новостей» не выдумываем
    try {
      const up = await api.upcoming();
      const u = diffUpcoming(fresh ? null : snap!.up, up.items, now);
      let proto = snap?.proto ?? {};
      if (tick % 3 === 0) {                                          // история — реже (раз в ~6 мин)
        const p = diffProtocols(fresh ? null : snap!.proto, await api.meetings(), now);
        proto = p.next;
        for (const n of p.notes) desktopNotify({ ...n, kind: "meeting" });
      }
      for (const n of u.notes) desktopNotify({ ...n, kind: "meeting" });
      snap = { up: u.next, proto, at: now };
      save(snap);
    } catch { /* сеть или сеанс: следующий опрос */ }
    tick += 1;
  };
  void run();
  const t = window.setInterval(() => void run(), intervalMs);
  return () => { stopped = true; window.clearInterval(t); };
}
