// Клиент REST API. Сессия — HttpOnly cookie (JS её не видит); CSRF-токен хранится только в памяти.

export interface User { id: string; sam_account_name: string; display_name: string; is_admin: boolean }
export interface Me { user: User; csrf_token: string }
export interface ActiveMeeting { id: string; started_at: string; participants: number }
export interface Room {
  id: string; slug: string; name: string; description: string | null; max_participants: number;
  has_password: boolean; transcription_enabled: boolean; record_audio: boolean;
  camera_allowed: boolean; screen_share_allowed: boolean; active_meeting: ActiveMeeting | null;
}
export interface ClientConfig { screen_profile: string; screen_share_audio: boolean; one_sharer_at_a_time: boolean }
export interface JoinInfo {
  meeting_id: string; room: Room; livekit_url: string; livekit_room: string; token: string; identity: string;
  recording: boolean; client: ClientConfig;
}
export interface ProtocolItem {
  id: string; meeting_id: string; kind: string; status: "pending" | "ready" | "failed"; error: string | null;
  created_by: string | null; created_at: string; updated_at: string; model: string | null; location: string | null; content?: string | null;
}
export interface AdminUser {
  id: string; sam_account_name: string; display_name: string; email: string | null; is_active: boolean; is_admin: boolean;
  last_login_at: string | null; ad_guid: string;
}
export interface DirHit { kind: "group" | "user"; ref: string; name: string; sam?: string; email?: string; description?: string }
export type SettingsGroup = "storage" | "anonymizer" | "llm" | "protocol" | "screen" | "general";
export type SettingsValues = Record<string, string | number | boolean | null>;
export interface TestResult { ok: boolean; message: string; ms: number }
export interface SystemStatus {
  version: string; commit: string; public_url: string; master_key_ok: boolean; disk_free_bytes: number | null;
  checks: Record<string, { ok: boolean; error?: string; [k: string]: unknown }>; counts: Record<string, number>;
}
export interface AuditRow { id: number; at: string; actor: string; action: string; target_type: string; target_id: string; details: unknown; ip: string | null }
export interface RecordingRow { id: string; meeting_id: string; room: string; identity: string; path: string; size_bytes: number; duration_s: number | null; created_at: string }
export interface Participant { user_id: string; display_name: string; joined_at: string; left_at: string | null; online: boolean }
export interface Meeting {
  id: string; room_id: string; room_name: string; started_at: string; ended_at: string | null;
  end_reason: string | null; transcription_enabled: boolean; participants: Participant[];
}
export interface Segment {
  id: number; uid: string; meeting_id: string; user_id: string | null; display_name: string; identity: string;
  started_at: string; ended_at: string; text: string; language: string | null;
}
export interface AclEntry { subject_type: "group" | "user"; subject_ref: string; display_name?: string | null }
export interface RoomAdmin {
  id: string; slug: string; name: string; description: string | null; is_enabled: boolean; max_participants: number;
  has_password: boolean; transcription_enabled: boolean; record_audio: boolean; camera_allowed: boolean;
  screen_share_allowed: boolean; text_retention_days: number | null; audio_retention_days: number | null;
  protocol_instructions: string | null; acl: AclEntry[]; active_meeting_id: string | null;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public retryAfter?: number) {
    super(message);
  }
}

let csrfToken = "";
export const setCsrf = (t: string) => { csrfToken = t; };

// Сессия истекла/прекращена админом: App переключает на экран входа вместо «молчаливых» ошибок.
let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };

/** «Выход при закрытии вкладки»: keepalive-запрос переживает выгрузку страницы. */
export function leaveOnUnload(meetingId: string): void {
  try {
    void fetch(`/api/v1/meetings/${meetingId}/leave`, { method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken } });
  } catch { /* страница закрывается */ }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером");
  }
  if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized?.();
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data?.detail;
    const code = typeof d === "object" && d ? d.code ?? "error" : "error";
    const message = typeof d === "object" && d ? d.message : typeof d === "string" ? d : `Ошибка ${res.status}`;
    const ra = Number(res.headers.get("Retry-After")) || undefined;
    throw new ApiError(res.status, code, message ?? `Ошибка ${res.status}`, ra);
  }
  return data as T;
}

export const api = {
  login: (login: string, password: string) => request<Me>("POST", "/auth/login", { login, password }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<Me>("GET", "/auth/me"),
  rooms: () => request<Room[]>("GET", "/rooms"),
  join: (roomId: string, password?: string) => request<JoinInfo>("POST", `/rooms/${roomId}/join`, { password: password || null }),
  leave: (meetingId: string) => request<void>("POST", `/meetings/${meetingId}/leave`),
  endMeeting: (meetingId: string) => request<void>("POST", `/meetings/${meetingId}/end`),
  meetings: (roomId?: string) => request<Meeting[]>("GET", `/meetings${roomId ? `?room_id=${roomId}` : ""}`),
  meeting: (id: string) => request<Meeting>("GET", `/meetings/${id}`),
  transcript: (id: string, afterId = 0) =>
    request<{ meeting_id: string; segments: Segment[]; has_more: boolean }>("GET", `/meetings/${id}/transcript?after_id=${afterId}&limit=2000`),
  version: () => request<{ version: string; commit: string }>("GET", "/version"),
  setRecording: (meetingId: string, enabled: boolean) => request<{ enabled: boolean }>("POST", `/meetings/${meetingId}/recording`, { enabled }),
  protocols: (meetingId: string) => request<ProtocolItem[]>("GET", `/meetings/${meetingId}/protocols`),
  protocol: (meetingId: string, id: string) => request<ProtocolItem>("GET", `/meetings/${meetingId}/protocols/${id}`),
  createSummary: (meetingId: string) => request<{ protocol_id: string }>("POST", `/meetings/${meetingId}/protocols/summary`),
  admin: {
    rooms: () => request<RoomAdmin[]>("GET", "/admin/rooms"),
    createRoom: (body: Record<string, unknown>) => request<RoomAdmin>("POST", "/admin/rooms", body),
    patchRoom: (id: string, body: Record<string, unknown>) => request<RoomAdmin>("PATCH", `/admin/rooms/${id}`, body),
    deleteRoom: (id: string) => request<void>("DELETE", `/admin/rooms/${id}`),
    settings: (g: SettingsGroup) => request<SettingsValues>("GET", `/admin/settings/${g}`),
    saveSettings: (g: SettingsGroup, body: SettingsValues) => request<SettingsValues>("PUT", `/admin/settings/${g}`, body),
    testSettings: (g: SettingsGroup) => request<TestResult>("POST", `/admin/settings/${g}/test`),
    users: (q = "") => request<AdminUser[]>("GET", `/admin/users?q=${encodeURIComponent(q)}`),
    setUserActive: (id: string, is_active: boolean) => request<{ id: string; is_active: boolean }>("PATCH", `/admin/users/${id}`, { is_active }),
    search: (kind: "group" | "user", q: string) => request<DirHit[]>("GET", `/admin/directory/search?kind=${kind}&q=${encodeURIComponent(q)}`),
    meetings: (active?: boolean) => request<Meeting[]>("GET", `/admin/meetings${active === undefined ? "" : `?active=${active}`}`),
    endMeeting: (id: string) => request<void>("POST", `/admin/meetings/${id}/end`),
    system: () => request<SystemStatus>("GET", "/admin/system"),
    audit: (offset = 0) => request<AuditRow[]>("GET", `/admin/audit?limit=100&offset=${offset}`),
    recordings: () => request<RecordingRow[]>("GET", "/admin/recordings"),
  },
};
