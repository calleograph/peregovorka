// Клиент REST API. Сессия — HttpOnly cookie (JS её не видит); CSRF-токен хранится только в памяти.

export interface User { id: string; sam_account_name: string; display_name: string; is_admin: boolean }
export interface Me { user: User; csrf_token: string }
export interface ActiveMeeting { id: string; started_at: string; participants: number }
export interface Room {
  id: string; slug: string; name: string; description: string | null; max_participants: number;
  has_password: boolean; transcription_enabled: boolean; record_audio: boolean;
  camera_allowed: boolean; screen_share_allowed: boolean; active_meeting: ActiveMeeting | null;
}
export interface ClientConfig {
  screen_profile: string; screen_share_audio: boolean; one_sharer_at_a_time: boolean;
  /** Руководитель комнаты или администратор: может выключать микрофоны участников. */
  can_moderate?: boolean; mute_on_join?: boolean; welcome_message?: string | null;
  /** Гость (вход по ссылке без AD): без административных функций, без показа экрана и стенограммы. */
  is_guest?: boolean;
}
export interface JoinInfo {
  meeting_id: string; room: Room; livekit_url: string; livekit_room: string; token: string; identity: string;
  recording: boolean; asr_ready: boolean; client: ClientConfig;
}
/** Ответ на вход гостя: то же, что у сотрудника, плюс сессия гостя (хранится только в этой вкладке). */
export interface GuestJoinInfo extends JoinInfo { guest_token: string; guest_id: string; display_name: string }
export interface GuestRoomInfo { room_name: string; description: string | null; meeting_active: boolean; has_password: boolean; camera_allowed: boolean }
export interface ChatMessage {
  id: number; meeting_id: string; created_at: string; author_type: "user" | "guest" | "system"; author_id: string | null; author_name: string; text: string;
}
/** Патч draw.io (diffSync), разосланный сервером. `from` — идентификатор вкладки-автора (чтобы не применять собственную правку повторно). */
export interface WhiteboardPatch { seq: number; patch: unknown; checksum: string | null; from: string; by: string }
export interface WhiteboardState {
  xml: string | null; seq: number; patches: WhiteboardPatch[]; active: boolean; used: boolean; shapes: number; updated_at: string | null; updated_by: string | null;
}
export type ProtocolKind = "summary" | "protocol";
export interface ProtocolItem {
  id: string; meeting_id: string; kind: ProtocolKind | string; status: "pending" | "ready" | "failed"; error: string | null;
  created_by: string | null; created_at: string; updated_at: string; model: string | null; location: string | null;
  title: string | null; edited_at: string | null; edited_by: string | null; content?: string | null; instruction?: string | null;
}
export interface ProtocolTemplate { id: string; name: string; kind: "any" | ProtocolKind; instruction: string; scope: "global" | "user"; can_edit: boolean }
export interface AdminUser {
  id: string; sam_account_name: string; display_name: string; email: string | null; is_active: boolean; is_admin: boolean;
  last_login_at: string | null; ad_guid: string;
}
export interface DirHit { kind: "group" | "user"; ref: string; name: string; sam?: string; email?: string; description?: string }
export type SettingsGroup = "storage" | "audio_storage" | "anonymizer" | "llm" | "protocol" | "screen" | "general" | "asr" | "journal";
export type SettingsValues = Record<string, string | number | boolean | null>;
export interface TestResult { ok: boolean; message: string; ms: number }
export interface TimingStat { n: number; avg: number; p95: number; max: number }
export interface SystemStatus {
  version: string; commit: string; built_at?: string; public_url: string; master_key_ok: boolean; disk_free_bytes: number | null;
  checks: Record<string, { ok: boolean; error?: string; [k: string]: unknown }>; counts: Record<string, number>;
  host?: { cpus?: number; load1?: number; load5?: number; load15?: number; mem_total?: number; mem_available?: number };
  kernel?: { ok: boolean; note: string; params: Record<string, { value: number | null; recommended: number; ok: boolean | null }> };
  timings?: Record<string, TimingStat | null>;
  versions?: { livekit_server: string; livekit_python_sdk_asr: string | null };
  live?: { users_online: number };
  recording_export?: { failed: number };
  realtime?: {
    active_meetings: number; users_online: number; counters: Record<string, number>; rooms_per_join: number;
    client: { samples: number; rtt_ms: number | null; packet_loss_pct: number | null; bitrate_out_kbps: number | null; bitrate_in_kbps: number | null };
    recording: { recorder_queue: number | null; recorder_dropped: number | null; recorder_written_mb: number | null };
  };
}
export interface AuditRow { id: number; at: string; actor: string; action: string; target_type: string; target_id: string; details: unknown; ip: string | null }
export interface RecordingRow {
  id: string; meeting_id: string; room: string; identity: string; path: string; size_bytes: number; duration_s: number | null; created_at: string;
  export_status?: string; export_location?: string | null; export_error?: string | null;
}
export interface MeetingRecording { id: string; identity: string; size_bytes: number; duration_s: number | null; name: string; export_status: string; export_error: string | null }
export interface Participant {
  user_id: string | null; guest_id?: string | null; participant_type?: "user" | "guest"; display_name: string; joined_at: string; left_at: string | null; online: boolean;
}
export interface Meeting {
  id: string; room_id: string; room_name: string; started_at: string; ended_at: string | null;
  end_reason: string | null; transcription_enabled: boolean; participants: Participant[];
  segments: number; recordings: number; protocols: number;
  /** Сообщений в чате встречи; доска «использовалась», если whiteboard_shapes > 0. */
  chat_messages?: number; whiteboard_shapes?: number; guests?: number;
}
export interface Segment {
  id: number; uid: string; meeting_id: string; user_id: string | null; guest_id?: string | null; display_name: string; identity: string;
  started_at: string; ended_at: string; text: string; language: string | null;
}
export interface AclEntry { subject_type: "group" | "user"; subject_ref: string; display_name?: string | null }
export type HistoryAccess = "admin" | "participants";
/** inherit — как в общих настройках; on — всегда обезличивать; off — не обезличивать (текст идёт в LLM как есть). */
export type AnonymizeMode = "inherit" | "on" | "off";
export interface ProtocolPlan { llm_ready: boolean; llm_profile: string; anonymize: boolean; anonymizer_profile: string | null; anonymizer_ready: boolean }

export interface JournalRow {
  id: number; at: string; level: "debug" | "info" | "warn" | "error"; category: string; event: string; user: string | null; room: string | null;
  meeting_id: string | null; ip: string | null; client: string | null; message: string | null; data: Record<string, unknown> | null; request_id?: string | null;
}
export type JournalOp = "eq" | "ne" | "contains" | "not_contains" | "starts" | "gte";
export interface JournalFilter { field: string; op: JournalOp; value: string | string[] }
export interface JournalQuery { filters: JournalFilter[]; q?: string; range?: string; since?: string; until?: string }
export interface JournalPage { items: JournalRow[]; next_cursor: number | null }
export interface JournalFacets { levels: string[]; categories: string[]; events: string[]; users: string[]; rooms: string[]; clients: string[] }
export interface JournalStats {
  total: number; last_24h: number; errors_24h: number; warns_24h: number; by_category_24h: Record<string, number>; oldest: string | null;
  size_bytes: number; avg_bytes_per_event: number; audit: { total: number; size_bytes: number | null }; retention_days: number; keep_local: boolean;
  external: { enabled: boolean; mode: string; ok: boolean | null; at: string | null; error: string | null; files: number }; queue_dropped: number; written_since_start: number;
}
export type ProfileKind = "llm" | "anonymizer";
export interface ApiProfile { id: string; kind: ProfileKind; name: string; config: Record<string, unknown>; secret_set: boolean; is_default: boolean; virtual: boolean }
export interface RoomAdmin {
  id: string; slug: string; name: string; description: string | null; is_enabled: boolean; max_participants: number;
  has_password: boolean; transcription_enabled: boolean; record_audio: boolean; camera_allowed: boolean;
  screen_share_allowed: boolean; text_retention_days: number | null; audio_retention_days: number | null;
  protocol_instructions: string | null; history_access: HistoryAccess; acl: AclEntry[]; active_meeting_id: string | null;
  anonymize_mode: AnonymizeMode; llm_profile_id: string | null; anonymizer_profile_id: string | null;
  mute_on_join: boolean; welcome_message: string | null; moderators: AclEntry[];
  guest_access_enabled: boolean; guest_token: string | null;
}
export interface Grant { user_id: string; display_name: string; sam_account_name: string; granted_by: string | null; created_at: string }
export interface ClientEventRow { ts: number; event: string; user: string; meeting_id: string | null; reason: string | null; detail: string | null }
export interface ClientMetricRow { ts: number; user: string; [k: string]: unknown }
export interface DiagnosticsReport {
  generated_at: string; verdict: string[]; [k: string]: unknown;
}

export type AsrModelStatus = "active" | "available" | "loading" | "missing" | "error" | "unsupported";
export interface AsrModel {
  id: string; title: string; runtime: string; quant: string; family: string; device: string; description: string; files: string[];
  present: boolean; missing: string[]; size_bytes: number; status: AsrModelStatus; error: string | null; load_ms: number | null;
  active: boolean; runtime_available: boolean;
}
export interface AsrModels {
  reachable: boolean; desired: string; active_id: string | null; loading_id?: string | null; device?: string; ready?: boolean; error?: string;
  threads?: { intra?: number; interop?: number }; test_audio_s?: number | null; models: AsrModel[];
  live?: { avg_infer_ms: number | null; avg_queue_ms: number | null; rtf: number | null; processed: number | null; dropped: number | null; queue_depth: number | null };
}
export interface AsrTestResult {
  ok: boolean; error?: string; skipped?: boolean; model_id: string; title: string; runtime: string; quant?: string; device?: string; audio_s?: number;
  load_ms?: number | null; inference_ms?: number; audio_duration_ms?: number; rtf?: number; cpu_s?: number; cpu_cores_avg?: number | null;
  ram_mb?: number | null; ram_delta_mb?: number | null; text?: string; reference?: string; wer?: number; cer?: number; repeat?: number; during_meeting?: boolean;
  punctuation?: { ref_marks: number; hyp_marks: number; precision: number; recall: number; f1: number };
}
export interface AsrCompare { ok: boolean; error?: string; audio_s?: number; reference?: string; results: AsrTestResult[]; summary: string[]; note?: string }

export interface UpdaterState {
  available: boolean; heartbeat_age_s: number | null; state: string | null; request_id?: string; step_no?: number; step_total?: number; step_name?: string;
  started_at?: number; finished_at?: number; exit_code?: number | null; result?: string; request_pending?: boolean; project?: string; by?: string;
}
export interface RemoteCommit { sha: string; date: string; subject: string }
export interface RemoteInfo {
  checked_at: number; age_s?: number; ok: boolean; error: string; branch: string; current: string; remote: string; behind: number; ahead: number;
  ff_possible: boolean; local_changes: number; migrations_changed: number; env_example_changed: boolean; commits: RemoteCommit[];
}
export interface UpdatesOverview {
  installed: { version: string; commit: string; built_at: string }; updater: UpdaterState; remote: RemoteInfo | null; active_meetings: number;
  can_update: boolean; reasons: string[]; up_to_date: boolean; commands: Record<string, string>;
}
export interface UpdateLog {
  offset: number; size: number; text: string; reset: boolean; state: string | null; step_no: number | null; step_total: number | null; step_name: string | null;
  exit_code: number | null; result: string | null; finished_at: number | null; available: boolean;
}
export interface ComponentRow {
  key: string; title: string; installed: string | null; tested: string | null; latest: string | null; status: "ok" | "newer" | "ahead" | "unknown"; pinned: boolean; note: string;
}
export interface ComponentsInfo { rows: ComponentRow[]; internet: boolean; fetched_at: number | null; tested: Record<string, string>; project_latest: string | null }

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public retryAfter?: number) {
    super(message);
  }
}

let csrfToken = "";
export const setCsrf = (t: string) => { csrfToken = t; };

// Сессия гостя: токен хранится только в памяти вкладки и отправляется заголовком (в cookie не кладётся — иначе вход гостем
// затёр бы сессию AD в том же браузере).
let guestToken = "";
export const setGuestToken = (t: string) => { guestToken = t; };
export const hasGuestToken = () => guestToken !== "";

// Сессия истекла/прекращена админом: App переключает на экран входа вместо «молчаливых» ошибок.
let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };

/** keepalive-запрос переживает выгрузку страницы (закрытие вкладки, переход). */
function keepalivePost(path: string): void {
  try {
    void fetch(`/api/v1${path}`, { method: "POST", keepalive: true, credentials: "same-origin", headers: guestToken ? { "X-Guest-Token": guestToken } : { "X-CSRF-Token": csrfToken } });
  } catch { /* страница закрывается */ }
}
/** «Выход при закрытии вкладки»: участник снимается с встречи. */
export const leaveOnUnload = (meetingId: string): void => keepalivePost(guestToken ? "/guest/session/leave" : `/meetings/${meetingId}/leave`);
/** Участник покинул страницу завершённой встречи: временный доступ к ней прекращается. */
export const releaseOnUnload = (meetingId: string): void => keepalivePost(`/meetings/${meetingId}/release`);

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (guestToken) headers["X-Guest-Token"] = guestToken;
  else if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
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

export type ExportFormat = "md" | "txt" | "docx" | "pdf";

/** Параметры запроса журнала (фильтры — JSON-строкой; пустые поля не передаются). */
export function journalParams(qy: JournalQuery, beforeId?: number, limit = 100): string {
  const p = new URLSearchParams({ limit: String(limit) });
  const filters = qy.filters.filter((f) => (Array.isArray(f.value) ? f.value.length > 0 : String(f.value).trim() !== ""));
  if (filters.length) p.set("filters", JSON.stringify(filters));
  if (qy.q?.trim()) p.set("q", qy.q.trim());
  if (qy.range) p.set("range", qy.range);
  if (qy.since) p.set("since", qy.since);
  if (qy.until) p.set("until", qy.until);
  if (beforeId) p.set("before_id", String(beforeId));
  return p.toString();
}

export const api = {
  login: (login: string, password: string) => request<Me>("POST", "/auth/login", { login, password }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<Me>("GET", "/auth/me"),
  rooms: () => request<Room[]>("GET", "/rooms"),
  join: (roomId: string, password?: string) => request<JoinInfo>("POST", `/rooms/${roomId}/join`, { password: password || null }),
  leave: (meetingId: string) => request<void>("POST", `/meetings/${meetingId}/leave`),
  /** Руководитель комнаты: выключить микрофоны у всех участников (кроме себя) или у одного. */
  muteAll: (meetingId: string) => request<{ muted: number }>("POST", `/meetings/${meetingId}/moderation/mute-all`),
  muteOne: (meetingId: string, identity: string) => request<{ muted: number }>("POST", `/meetings/${meetingId}/moderation/mute`, { identity }),
  endMeeting: (meetingId: string) => request<void>("POST", `/meetings/${meetingId}/end`),
  meetings: (roomId?: string, offset = 0) => request<Meeting[]>("GET", `/meetings?limit=30&offset=${offset}${roomId ? `&room_id=${roomId}` : ""}`),
  meeting: (id: string) => request<Meeting>("GET", `/meetings/${id}`),
  transcript: (id: string, afterId = 0) =>
    request<{ meeting_id: string; segments: Segment[]; has_more: boolean }>("GET", `/meetings/${id}/transcript?after_id=${afterId}&limit=2000`),
  transcriptExportUrl: (id: string, fmt: ExportFormat) => `/api/v1/meetings/${id}/transcript/export?format=${fmt}`,
  version: () => request<{ version: string; commit: string }>("GET", "/version"),
  chat: (id: string, q: { beforeId?: number; afterId?: number; limit?: number } = {}) =>
    request<{ messages: ChatMessage[]; has_more: boolean }>("GET", `/meetings/${id}/chat?limit=${q.limit ?? 100}${q.beforeId ? `&before_id=${q.beforeId}` : ""}${q.afterId ? `&after_id=${q.afterId}` : ""}`),
  sendChat: (id: string, text: string) => request<ChatMessage>("POST", `/meetings/${id}/chat`, { text }),
  chatTextUrl: (id: string) => `/api/v1/meetings/${id}/chat.txt`,
  whiteboard: (id: string) => request<WhiteboardState>("GET", `/meetings/${id}/whiteboard`),
  whiteboardPatch: (id: string, body: { patch: unknown; checksum?: string | null; client_id: string }) =>
    request<{ seq: number }>("POST", `/meetings/${id}/whiteboard/patch`, body),
  whiteboardSave: (id: string, xml: string, seq: number) =>
    request<{ saved: boolean; seq: number; shapes: number; used: boolean }>("PUT", `/meetings/${id}/whiteboard`, { xml, seq }),
  whiteboardFileUrl: (id: string) => `/api/v1/meetings/${id}/whiteboard.drawio`,
  setRecording: (meetingId: string, enabled: boolean) => request<{ enabled: boolean }>("POST", `/meetings/${meetingId}/recording`, { enabled }),

  protocols: (meetingId: string) => request<ProtocolItem[]>("GET", `/meetings/${meetingId}/protocols`),
  protocol: (meetingId: string, id: string) => request<ProtocolItem>("GET", `/meetings/${meetingId}/protocols/${id}`),
  defaultInstruction: (meetingId: string, kind: ProtocolKind) =>
    request<{ kind: string; instruction: string; plan?: ProtocolPlan }>("GET", `/meetings/${meetingId}/protocols/default-instruction?kind=${kind}`),
  createProtocol: (meetingId: string, kind: ProtocolKind, instruction: string) =>
    request<{ protocol_id: string }>("POST", `/meetings/${meetingId}/protocols`, { kind, instruction }),
  editProtocol: (meetingId: string, id: string, body: { content?: string; title?: string }) =>
    request<ProtocolItem>("PATCH", `/meetings/${meetingId}/protocols/${id}`, body),
  deleteProtocol: (meetingId: string, id: string) => request<void>("DELETE", `/meetings/${meetingId}/protocols/${id}`),
  protocolExportUrl: (meetingId: string, id: string, fmt: ExportFormat) => `/api/v1/meetings/${meetingId}/protocols/${id}/export?format=${fmt}`,

  templates: () => request<ProtocolTemplate[]>("GET", "/protocol-templates"),
  createTemplate: (body: { name: string; instruction: string; kind: string; scope: "global" | "user" }) => request<ProtocolTemplate>("POST", "/protocol-templates", body),
  updateTemplate: (id: string, body: Partial<{ name: string; instruction: string; kind: string }>) => request<ProtocolTemplate>("PUT", `/protocol-templates/${id}`, body),
  deleteTemplate: (id: string) => request<void>("DELETE", `/protocol-templates/${id}`),

  meetingRecordings: (id: string) => request<MeetingRecording[]>("GET", `/meetings/${id}/recordings`),
  recordingUrl: (meetingId: string, recId: string) => `/api/v1/meetings/${meetingId}/recordings/${recId}`,
  deleteRecordings: (id: string) => request<void>("DELETE", `/meetings/${id}/recordings`),
  deleteMeeting: (id: string) => request<void>("DELETE", `/meetings/${id}`),

  // Клиентская диагностика: ошибки и метрики не должны влиять на работу, поэтому тихо игнорируются.
  // Гость в диагностику не пишет (эндпоинты для сотрудников; 401 означал бы «сессия гостя истекла»).
  clientEvent: (body: Record<string, unknown>) => { if (!guestToken) void request<void>("POST", "/client/events", body).catch(() => undefined); },
  clientMetrics: (body: Record<string, unknown>) => { if (!guestToken) void request<void>("POST", "/client/metrics", body).catch(() => undefined); },

  guest: {
    room: (token: string) => request<GuestRoomInfo>("GET", `/guest/room/${encodeURIComponent(token)}`),
    join: (token: string, displayName: string, password?: string) =>
      request<GuestJoinInfo>("POST", `/guest/room/${encodeURIComponent(token)}/join`, { display_name: displayName, password: password || null }),
    rejoin: () => request<GuestJoinInfo>("POST", "/guest/session/rejoin"),
    leave: () => request<void>("POST", "/guest/session/leave"),
  },

  admin: {
    rooms: () => request<RoomAdmin[]>("GET", "/admin/rooms"),
    guestLink: (id: string, action: "rotate" | "revoke") => request<RoomAdmin>("POST", `/admin/rooms/${id}/guest-link/${action}`),
    createRoom: (body: Record<string, unknown>) => request<RoomAdmin>("POST", "/admin/rooms", body),
    patchRoom: (id: string, body: Record<string, unknown>) => request<RoomAdmin>("PATCH", `/admin/rooms/${id}`, body),
    deleteRoom: (id: string) => request<void>("DELETE", `/admin/rooms/${id}`),
    settings: (g: SettingsGroup) => request<SettingsValues>("GET", `/admin/settings/${g}`),
    saveSettings: (g: SettingsGroup, body: SettingsValues) => request<SettingsValues>("PUT", `/admin/settings/${g}`, body),
    testSettings: (g: SettingsGroup) => request<TestResult>("POST", `/admin/settings/${g}/test`),
    users: (q = "", offset = 0) => request<AdminUser[]>("GET", `/admin/users?q=${encodeURIComponent(q)}&limit=100&offset=${offset}`),
    setUserActive: (id: string, is_active: boolean) => request<{ id: string; is_active: boolean }>("PATCH", `/admin/users/${id}`, { is_active }),
    search: (kind: "group" | "user", q: string) => request<DirHit[]>("GET", `/admin/directory/search?kind=${kind}&q=${encodeURIComponent(q)}`),
    meetings: (active?: boolean, offset = 0) => request<Meeting[]>("GET", `/admin/meetings?limit=50&offset=${offset}${active === undefined ? "" : `&active=${active}`}`),
    endMeeting: (id: string) => request<void>("POST", `/admin/meetings/${id}/end`),
    grants: (id: string) => request<Grant[]>("GET", `/admin/meetings/${id}/grants`),
    addGrant: (id: string, userId: string) => request<{ ok: boolean }>("POST", `/admin/meetings/${id}/grants`, { user_id: userId }),
    removeGrant: (id: string, userId: string) => request<void>("DELETE", `/admin/meetings/${id}/grants/${userId}`),
    system: () => request<SystemStatus>("GET", "/admin/system"),
    audit: (offset = 0, f: { q?: string; actor?: string; action?: string } = {}) =>
      request<AuditRow[]>("GET", `/admin/audit?limit=100&offset=${offset}&q=${encodeURIComponent(f.q ?? "")}&actor=${encodeURIComponent(f.actor ?? "")}&action=${encodeURIComponent(f.action ?? "")}`),
    recordings: (offset = 0) => request<RecordingRow[]>("GET", `/admin/recordings?limit=100&offset=${offset}`),

    journal: (qy: JournalQuery, beforeId?: number, limit = 100) => request<JournalPage>("GET", `/admin/journal?${journalParams(qy, beforeId, limit)}`),
    journalFacets: () => request<JournalFacets>("GET", "/admin/journal/facets"),
    journalStats: () => request<JournalStats>("GET", "/admin/journal/stats"),
    journalDelete: (body: { ids: number[] } | ({ all_matching: true } & JournalQuery)) => request<{ deleted: number }>("POST", "/admin/journal/delete", body),
    journalPurge: () => request<{ db: number; external_days: number }>("POST", "/admin/journal/purge-now"),
    journalExportUrl: (range: "24h" | "7d" | "30d" | "all") => `/api/v1/admin/journal/export?range=${range}`,

    updates: () => request<UpdatesOverview>("GET", "/admin/updates"),
    updatesCheck: () => request<{ request_id: string }>("POST", "/admin/updates/check"),
    updatesRun: (body: { confirm: true; force_build: boolean; pull: boolean }) => request<{ request_id: string }>("POST", "/admin/updates/run", body),
    updatesLog: (offset: number) => request<UpdateLog>("GET", `/admin/updates/log?offset=${offset}`),
    updateComponents: (refresh = false) => request<ComponentsInfo>("GET", `/admin/updates/components${refresh ? "?refresh=true" : ""}`),

    profiles: (kind: ProfileKind) => request<ApiProfile[]>("GET", `/admin/api-profiles?kind=${kind}`),
    createProfile: (body: { kind: ProfileKind; name: string; config: Record<string, unknown>; secret?: string; make_default?: boolean }) => request<ApiProfile>("POST", "/admin/api-profiles", body),
    updateProfile: (id: string, body: { name?: string; config?: Record<string, unknown>; secret?: string | null }) => request<ApiProfile>("PATCH", `/admin/api-profiles/${id}`, body),
    deleteProfile: (id: string) => request<void>("DELETE", `/admin/api-profiles/${id}`),
    setDefaultProfile: (kind: ProfileKind, profileId: string) => request<{ ok: boolean }>("PUT", "/admin/api-profiles/default", { kind, profile_id: profileId }),
    testProfile: (kind: ProfileKind, id: string) => request<TestResult>("POST", `/admin/api-profiles/${id}/test?kind=${kind}`),
    retryExports: () => request<{ exported: number; still_failed: number }>("POST", "/admin/recordings/retry-exports"),
    runRetention: () => request<Record<string, number>>("POST", "/admin/retention/run"),
    clientDiagnostics: () => request<{ events: ClientEventRow[]; metrics: ClientMetricRow[]; lifecycle: ClientEventRow[] }>("GET", "/admin/client-diagnostics"),
    diagnosticsReport: () => request<DiagnosticsReport>("GET", "/admin/diagnostics/report"),
    asrModels: () => request<AsrModels>("GET", "/admin/asr/models"),
    setAsrModel: (modelId: string) => request<{ ok: boolean; desired: string; note: string }>("PUT", "/admin/asr/active", { model_id: modelId }),
    asrTest: (modelId?: string, force = false) => request<AsrTestResult>("POST", "/admin/asr/test", { model_id: modelId, force }),
    asrCompare: (force = false) => request<AsrCompare>("POST", "/admin/asr/compare", { force }),
  },
};
