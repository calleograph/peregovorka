// Клиент API календаря (/api/v1/calendar). Типы повторяют ответы backend (api/calendar.py, services/calendar_svc.py).
import { http } from "./api";
import type { CalEvent } from "./calendarView";

export interface CalendarStatus {
  available: boolean; reason: "disabled" | "no_connection" | "group" | "no_email" | null; message: string | null; connected: boolean; email: string | null;
  can_write: boolean; mode: "off" | "graph" | "ews"; guests_allowed: boolean; last_ok_at: string | null; last_error: string | null;
}
export interface CalendarEvents { events: CalEvent[]; from: string; to: string; server_time: string; stale: boolean; error: string | null; mode: string; last_ok_at?: string | null }
export interface CalendarInfo { id: string; name: string; default: boolean; can_edit: boolean }
export interface Attendee { email: string; name: string; type: "required" | "optional"; response: string }
export interface EventCard extends Omit<CalEvent, "organizer"> { organizer: { email: string; name: string | null } | null; organizer_is_me: boolean; attendees: Attendee[]; description: string; private: boolean; can_edit: boolean }
export type Scope = "occurrence" | "series";
export interface CreatedEvent { id: string | null; room_id: string | null; title: string; start: string; end: string; recurring: boolean; join_url: string | null; guest_url: string | null; duplicate: boolean; skipped?: string[] }

const C = "/calendar";
export const calendarApi = {
  status: () => http.request<CalendarStatus>("GET", `${C}/status`),
  connect: () => http.request<CalendarStatus>("POST", `${C}/connect`, { accept: true }),
  disconnect: () => http.request<CalendarStatus>("DELETE", `${C}/connect`),
  calendars: (refresh = false) => http.request<{ calendars: CalendarInfo[]; error: string | null }>("GET", `${C}/calendars${refresh ? "?refresh=true" : ""}`),
  events: (from: Date, to: Date, refresh = false, calendars: string[] = []) =>
    http.request<CalendarEvents>("GET", `${C}/events?from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}${refresh ? "&refresh=true" : ""}${calendars.length ? `&calendars=${encodeURIComponent(calendars.join(","))}` : ""}`),
  open: (id: string) => http.request<EventCard>("POST", `${C}/event/open`, { id }),
  update: (body: Record<string, unknown>) => http.request<{ id: string; etag: string | null; scope: string }>("POST", `${C}/event/update`, body),
  cancelEvent: (body: { id: string; scope?: Scope; comment?: string }) => http.request<{ id: string; cancelled: boolean; scope: string }>("POST", `${C}/event/cancel`, body),
  create: (body: Record<string, unknown>) => http.request<CreatedEvent>("POST", `${C}/events`, body),
  reschedule: (roomId: string, body: { subject?: string; start?: string; end?: string; etag?: string | null }) => http.request<{ room_id: string; changed: boolean }>("PATCH", `${C}/events/${roomId}`, body),
  cancel: (roomId: string) => http.request<{ room_id: string; cancelled: boolean }>("DELETE", `${C}/events/${roomId}`),
};
