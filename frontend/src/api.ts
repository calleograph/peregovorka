// Клиент REST API. Сессия — HttpOnly cookie (JS её не видит); CSRF-токен хранится только в памяти.

export interface User { id: string; sam_account_name: string; display_name: string; is_admin: boolean }
export interface Me { user: User; csrf_token: string; local?: boolean; must_change_password?: boolean }
export interface ActiveMeeting { id: string; started_at: string; participants: number }
export interface Room {
  id: string; slug: string; name: string; description: string | null; max_participants: number;
  has_password: boolean; transcription_enabled: boolean; record_audio: boolean;
  camera_allowed: boolean; screen_share_allowed: boolean; active_meeting: ActiveMeeting | null;
  board_allowed?: boolean; board_access?: string; room_type?: "regular" | "presentation"; auto_record?: boolean;
  /** temporary — временная переговорка (создана пользователем на одну встречу); lifecycle: active → grace_period → closed. */
  lifetime?: "permanent" | "temporary"; lifecycle?: "active" | "grace_period" | "closed"; auto_close_at?: string | null; created_by_name?: string | null;
  /** Только в списке комнат: вошедший — руководитель/администратор; секрет гостевой ссылки отдаётся только им и только при включённом гостевом входе. */
  can_manage?: boolean; guest_token?: string | null;
}
export interface RoomRef { id: string; slug: string; name: string; canonical: boolean; lifetime: "permanent" | "temporary"; lifecycle: "active" | "grace_period" | "closed"; room?: Room }
export interface TempRoomPolicy { enabled: boolean; max_per_user: number; active_mine: number; can_create: boolean; grace_minutes: number }
export interface ClientConfig {
  screen_profile: string; screen_share_audio: boolean; one_sharer_at_a_time: boolean;
  /** Руководитель комнаты или администратор: может выключать микрофоны участников. */
  can_moderate?: boolean; mute_on_join?: boolean; welcome_message?: string | null;
  /** Гость (вход по ссылке без AD): без административных функций, без показа экрана и стенограммы. */
  is_guest?: boolean;
  /** Руководитель комнаты или администратор: «Настройки комнаты», участники встречи, слово. */
  can_manage?: boolean;
  /** Может переключать запись и транскрибацию. */
  can_control?: boolean;
  /** Презентационная комната: участники — слушатели, пока им не «дали слово». */
  presentation?: boolean;
  /** Что можно публиковать сейчас: microphone | camera | screen_share | screen_share_audio. */
  sources?: string[];
  /** Вам дано слово. */
  floor?: boolean;
  can_edit_board?: boolean;
  /** Видна ли доска (при «только у руководителей» остальные её не видят) и действующий уровень: everyone | speakers | leaders | private. */
  can_view_board?: boolean; board_access?: string;
  /** Комната допускает запись аудио (кнопка «Начать запись»). */
  recording_allowed?: boolean;
  /** Вложения в чат включены. */
  attachments?: boolean;
}
export interface JoinInfo {
  meeting_id: string; room: Room; livekit_url: string; livekit_room: string; token: string; identity: string;
  recording: boolean; transcription?: boolean; asr_ready: boolean; client: ClientConfig;
}
/** Ответ на вход гостя: то же, что у сотрудника, плюс сессия гостя (хранится только в этой вкладке). */
export interface GuestJoinInfo extends JoinInfo { guest_token: string; guest_id: string; display_name: string }
export interface GuestRoomInfo { room_name: string; description: string | null; meeting_active: boolean; has_password: boolean; camera_allowed: boolean }
export interface ChatAttachment { id: string; name: string; mime: string; size: number; kind: "image" | "file"; missing?: boolean }
export interface ChatMessage {
  id: number; meeting_id: string; created_at: string; author_type: "user" | "guest" | "system"; author_id: string | null; author_name: string; text: string;
  attachments?: ChatAttachment[];
}
export interface FloorState { presentation: boolean; floor: string[]; leaders: string[] }
/** Патч draw.io (diffSync), разосланный сервером. `from` — идентификатор вкладки-автора (чтобы не применять собственную правку повторно). */
export interface WhiteboardPatch { seq: number; patch: unknown; checksum: string | null; from: string; by: string }
export interface WhiteboardState {
  xml: string | null; seq: number; patches: WhiteboardPatch[]; active: boolean; used: boolean; shapes: number; updated_at: string | null; updated_by: string | null;
}
export type ProtocolKind = "summary" | "protocol";
export interface Timing {
  requested_at: string | null; started_at: string | null; llm_started_at: string | null; llm_finished_at: string | null; finished_at: string | null;
  queue_s: number | null; prepare_s: number | null; llm_s: number | null; total_s: number | null;
}
export interface Generation {
  model: string | null; model_title: string | null; llm_local: boolean | null; api_type: string | null; llm_profile: string | null; llm_source: string | null;
  llm_once: string | null; chunks: number | null; llm_calls: number | null; retries: number | null; length_hits: number | null; finish: Record<string, number> | null;
  max_tokens: number | null; limit_note: string | null; input_chars: number | null; output_chars: number | null; prompt_tokens: number | null;
  completion_tokens: number | null; structured: boolean | null; failed: boolean | null; error_code: string | null;
}
export interface ProtocolItem {
  id: string; meeting_id: string; kind: ProtocolKind | string; status: "pending" | "ready" | "failed"; error: string | null;
  created_by: string | null; created_at: string; updated_at: string; model: string | null; location: string | null;
  title: string | null; edited_at: string | null; edited_by: string | null; content?: string | null; instruction?: string | null;
  /** Предупреждения при формировании: например, локальная модель не смогла полностью обработать стенограмму (truncated). */
  warnings?: string[]; truncated?: boolean;
  /** Времена этапов (ISO) и длительности в секундах: нажатие → начало обработки → начало работы модели → готово; очередь, подготовка, модель, всего. */
  timing?: Timing;
  /** Как создан документ: модель, локальная ли, тип API, части, повторы, причины остановки. */
  generation?: Generation;
  /** Выгруженный файл удалён из хранилища (по сверке): ссылки `location` нет; сам текст остаётся в системе. */
  file_state?: "ok" | "missing";
}
export interface ProtocolTemplate { id: string; name: string; kind: "any" | ProtocolKind; instruction: string; scope: "global" | "user"; can_edit: boolean }
export interface AdminUser {
  id: string; sam_account_name: string; display_name: string; email: string | null; is_active: boolean; is_admin: boolean;
  last_login_at: string | null; ad_guid: string;
}
export interface DirHit { kind: "group" | "user"; ref: string; name: string; sam?: string; email?: string; description?: string }
export type SettingsGroup = "autoupdate" | "privacy" | "access" | "mail_policy" | "storage_sync" | "storage" | "audio_storage" | "chat_files" | "anonymizer" | "llm" | "protocol" | "screen" | "general" | "asr" | "journal" | "bitrix24";
export interface BitrixLookup { ok: boolean; message: string; fields?: Record<string, string>; external_id?: string | null; has_photo?: boolean }
export interface BitrixSync { status: string; processed: number; result: Record<string, number> }
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
  export_status?: string; export_location?: string | null; export_error?: string | null; file_state?: "ok" | "missing";
}
export interface MeetingRecording { id: string; identity: string; size_bytes: number; duration_s: number | null; name: string; export_status: string; export_error: string | null; file_state?: "ok" | "missing" }
export interface Participant {
  user_id: string | null; guest_id?: string | null; participant_type?: "user" | "guest" | "phone"; role?: "organizer" | "leader" | null; display_name: string; joined_at: string; left_at: string | null; online: boolean;
}
export interface Meeting {
  id: string; room_id: string; room_name: string; started_at: string; ended_at: string | null;
  end_reason: string | null; transcription_enabled: boolean; participants: Participant[];
  segments: number; recordings: number; protocols: number;
  /** Сообщений в чате встречи; доска «использовалась», если whiteboard_shapes > 0. */
  chat_messages?: number; whiteboard_shapes?: number; guests?: number; can_send_materials?: boolean;
}
export interface Segment {
  id: number; uid: string; meeting_id: string; user_id: string | null; guest_id?: string | null; display_name: string; identity: string;
  started_at: string; ended_at: string; text: string; language: string | null;
}
/** Карта разговора: ответ сервера (данные карты — проверенный JSON, показывает окно /mapview). */
export interface MapState {
  status: "none" | "pending" | "running" | "ready" | "failed";
  finished: boolean; can_edit: boolean;
  plan: { ready: boolean; reason: string | null; model: string | null; profile: string; local: boolean; source: string };
  categories: { id: string; label: string }[];
  data?: unknown; meta?: Record<string, unknown> | null; error?: string | null; created_by?: string | null; updated_at?: string;
}
export interface MapTopicEdit { title?: string; category?: string | null; note?: string }
export interface AclEntry { subject_type: "group" | "user"; subject_ref: string; display_name?: string | null }
export type HistoryAccess = "admin" | "participants";
/** inherit — как в общих настройках; on — всегда обезличивать; off — не обезличивать (текст идёт в LLM как есть). */
export type AnonymizeMode = "inherit" | "on" | "off";
export interface Forecast { low_s: number; high_s: number; text: string; basis: "history" | "estimate"; samples: number; chunks: number; note: string }
export interface LlmChoiceOnce { key: string; label: string; local: boolean }
export interface ProtocolPlan {
  llm_ready: boolean; llm_profile: string; anonymize: boolean; anonymizer_profile: string | null; anonymizer_ready: boolean;
  forecast?: Forecast; max_output_tokens?: number; max_output_note?: string; llm_api_type?: string; llm_source?: string; once?: boolean;
  /** Локальная встроенная модель: данные не покидают сервер. warnings — например, длинная стенограмма для облегчённой модели. */
  llm_local?: boolean; llm_model?: string | null; warnings?: string[]; input_chars?: number | null;
}

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
export type RoomType = "regular" | "presentation";
export interface StorageProfile {
  id: string; name: string; kind: "local" | "smb"; config: Record<string, string>; secret_set: boolean; used_by: string[]; address: string;
}
/** Настройки комнаты для её руководителя («Настройки комнаты»): без системных полей (хранилища, LLM, сроки хранения). */
export interface RoomManage {
  id: string; slug: string; name: string; description: string | null; is_enabled: boolean; max_participants: number; has_password: boolean;
  camera_allowed: boolean; screen_share_allowed: boolean; board_allowed: boolean; board_access?: string; board_level?: string; room_type: RoomType; auto_record: boolean; record_audio: boolean;
  transcription_enabled: boolean; mute_on_join: boolean; welcome_message: string | null; guest_access_enabled: boolean; guest_token: string | null; auto_map_mode?: "inherit" | "on" | "off";
  lifetime?: "permanent" | "temporary"; lifecycle?: "active" | "grace_period" | "closed";
  acl: AclEntry[]; moderators: AclEntry[]; active_meeting_id: string | null; can_edit_system_fields: boolean; needs_rejoin?: boolean;
  mail_delivery?: MailDeliverySpec | null;
  protocol_instructions?: string | null;
  /** Сроки хранения и обезличивание задаёт администратор: руководителю показываются только для сведения. */
  retention?: { text_days: number | null; audio_days: number | null; history_access: string; anonymize_mode: string } | null;
  llm?: LlmChoice | null; llm_effective?: LlmEffective | null; llm_options?: LlmOptions | null;
  /** Модель для краткого резюме — отдельная цепочка «система → комната → встреча». */
  llm_summary?: LlmChoice | null; llm_summary_effective?: LlmEffective | null;
  sip?: RoomSip | null; sip_options?: SipOptions | null;
}
/** Выбор языковой модели: inherit — системная по умолчанию; local — локальная модель; profile — внешний профиль; off — отключена. */
export interface LlmChoice { mode: "inherit" | "local" | "profile" | "off"; profile_id: string | null; local_model: string | null }
export interface LlmEffective { name: string; source: "system" | "room" | "meeting"; available: boolean; reason: string | null; note: string | null }
export interface LlmOptions {
  system: { name: string; provider: "local" | "external" | "off"; model: string | null };
  system_summary?: { name: string; provider: "local" | "external" | "off"; model: string | null };
  local: { id: string; title: string; light: boolean; installed: boolean }[];
  profiles: { id: string; name: string; model: string; is_default: boolean }[];
  on_missing: "system" | "unavailable";
}
export interface RoomSip {
  mode: "off" | "default" | "profile"; profile_id: string | null; extension: string | null; allow_inbound: boolean; allow_outbound: boolean; contacts: { name: string; number: string }[];
}
export interface SipOptions { server_enabled: boolean; profiles: { id: string; name: string; direction: string; is_default: boolean; synced: boolean }[]; default: string | null }
export interface MeetingSettings {
  ended: boolean;
  delivery: { effective: MailDeliverySpec; room: MailDeliverySpec; override: boolean };
  llm: { effective: LlmEffective; room: LlmChoice; override: LlmChoice | null };
  llm_summary: { effective: LlmEffective; room: LlmChoice; override: LlmChoice | null };
  llm_options: LlmOptions;
}
export interface PhoneState {
  can_call: boolean; reason: string | null; profile: string | null; contacts: { name: string; number: string }[]; extension: string | null; allow_inbound: boolean;
  active_meeting_id: string | null; phones: { guest_id: string; display_name: string }[]; allowed_prefixes: string[];
}
export interface SipProfile {
  id: string; name: string; enabled: boolean; is_default: boolean; direction: "outbound" | "inbound" | "both"; host: string; port: number; transport: "udp" | "tcp" | "tls";
  username: string; realm: string; caller_id: string; allowed_numbers: string[]; inbound_numbers: string[]; allowed_addresses: string[]; codecs: string[];
  media_encryption: "disable" | "allow" | "require"; ring_timeout_s: number; secret_set: boolean; synced: boolean; rooms_using?: number;
  last_check: { ok: boolean; message?: string; test_call?: boolean; stages?: DiagStage[] } | null; last_check_at: string | null; sync?: { ok: boolean | null; message: string };
}
export interface SipStatus {
  enabled_on_server: boolean; ports: { signaling_port: number; rtp_start: number; rtp_end: number; media_ip: string | null; allowed_cidrs: string[] };
  service: { running: boolean | null; detail: string }; livekit: { ok: boolean | null; detail: string }; redis: { ok: boolean | null; detail: string };
  trunks: { profiles: number; enabled: number; synced: number; in_livekit?: number; missing?: string[] };
  last_check: { profile: string; result: { ok: boolean; message?: string }; at: string | null } | null; active_trunk: string | null; codecs_supported: string[];
}
export type ProfileKind = "llm" | "anonymizer";
export interface LdapProfile {
  id: string; name: string; enabled: boolean; host: string; port: number; protocol: "ldaps" | "starttls"; base_dn: string; upn_suffix: string; netbios_domain: string;
  timeout_s: number; bind_dn: string; secret_set: boolean; login_attribute: string; display_name_attribute: string; email_attribute: string;
  use_for_users: boolean; use_for_admins: boolean; position: number; uri: string;
}
export interface DiagStage { stage: string; ok: boolean | null; message: string; ms?: number }
export interface DiagResult { ok: boolean; stages: DiagStage[]; stage?: string; message?: string }
export interface CaCert {
  id: string; label: string; subject: string; issuer: string; serial: string; sha256: string; not_before: string; not_after: string; is_ca: boolean; self_signed: boolean;
  expired: boolean; added_by: string | null; created_at: string | null; source: "web" | "file"; type?: string;
}
export interface CaInfo { subject: string; issuer: string; serial: string; sha256: string; not_before: string; not_after: string; is_ca: boolean; self_signed: boolean; expired: boolean; not_yet_valid: boolean; type: string }
export interface LocalAdminInfo { exists: boolean; username?: string; is_active?: boolean; must_change_password?: boolean; last_login_at?: string | null; password_changed_at?: string | null; recovery: string }
export interface AccessStatus { restricted: boolean; user_groups: number; admin_groups: number; env_user_group: boolean; env_admin_group: boolean }
export interface AccessCheck {
  found: boolean; message?: string; login?: string; display_name?: string; source?: string; account_disabled?: boolean; restricted?: boolean;
  would_log_in?: boolean; reason?: string; allowed_via?: string[]; admin?: boolean; admin_via?: string[]; groups_total?: number;
}
export interface Profile {
  id: string; display_name: string; login: string; email: string | null; title: string | null; department: string | null; phone: string | null;
  source: "ad" | "local"; avatar_url: string | null; synced_at: string | null; is_admin: boolean;
}
export interface ParticipantCard {
  identity: string; name: string; guest: boolean; card: "full" | "minimal";
  title?: string | null; department?: string | null; avatar_url?: string | null; email?: string | null; phone?: string | null; login?: string | null;
}
export interface HandInfo { identity: string; name: string; at: number }
export interface LlmChoices {
  local: { id: string; title: string; installed: boolean }[];
  external: { id: string; name: string; model: string; type: string | null; secret_set: boolean; virtual: boolean; host: string }[];
  tasks: Record<"protocol" | "summary" | "map", { mode: "local" | "external" | "off"; profile_id: string | null; same_as_protocol: boolean }>;
  on_missing: "system" | "unavailable"; limits: Record<string, number | null>;
}
export interface LlmDiagnose {
  ok: boolean;
  config: { provider: string; type: string; model: string; host: string; max_output_tokens: number; context_window: number | null; temperature: number | string;
    capabilities: { system: boolean; json: boolean; temperature: boolean }; timeout_s: number };
  tests: { name: string; ok: boolean; message: string; ms: number }[];
}
export interface EffectiveModel {
  label?: string; profile_id?: string | null; context_window?: number | null;
  enabled: boolean; local: boolean; name: string; model: string | null; api_type: string | null; max_output_tokens: number | null; max_output_note: string;
  ready: boolean; problem: string | null;
}
export interface EffectiveModels { protocol: EffectiveModel; summary: EffectiveModel; map: EffectiveModel; rooms_with_own_model: { protocol: number; summary: number; map: number } }
export interface ModelStat {
  model: string; local: boolean; profile: string; title: string; kind: string; documents: number; ok: number; failed: number; truncated: number; length_hits: number;
  retries: number; avg_s: number | null; median_s: number | null; avg_input_chars: number | null; avg_output_chars: number | null; tokens_per_s: number | null; last: string | null; archived?: boolean;
}
export interface SetupStep { id: string; title: string; done: boolean; page: string }
export interface SetupStatus { completed: boolean; skipped: string[]; steps: SetupStep[]; show: boolean }
export interface MailProfile {
  id: string; name: string; host: string; port: number; security: "none" | "starttls" | "ssl"; auth_type: "none" | "login"; username: string; secret_set: boolean;
  from_address: string; from_name: string; timeout_s: number; verify_cert: boolean; is_active: boolean;
}
export interface MailMessageRow {
  id: string; created_at: string; sent_at: string | null; state: "queued" | "sending" | "sent" | "failed"; attempts: number; max_attempts: number; next_attempt_at: string;
  recipient: string; recipient_name: string | null; room: string | null; meeting_id: string | null; subject: string; kinds: string[]; trigger: string;
  requested_by: string | null; last_error: string | null; delivery: string | null;
}
export interface MailTemplate { id: string; name: string; subject: string; body: string; signature: string; materials: string[]; is_default: boolean }
export interface DeliveryTemplate { id: string; name: string; is_default: boolean; subject: string; body: string; materials: string[] }
export interface DeliveryLogRow {
  id: string; batch_id: string | null; at: string; sent_at: string | null; by: string | null; trigger: string; recipient: string; name: string | null; state: string; attempts: number;
  error: string | null; kinds: string[]; template: string | null; formats: string[] | null; delivery: string | null; subject: string;
}
export interface MailDeliverySpec {
  enabled: boolean; archive?: boolean; materials: string[]; template_id?: string | null; formats?: string[]; if_missing?: "send" | "skip";
  recipients: { leaders: boolean; participants: boolean; users: { ref: string; name: string; email: string }[]; emails: string[] };
}
export interface DeliveryRecipient { email: string; name: string; source: string; problem: string | null }
export interface DeliveryMaterialInfo { kind: string; label: string; describe?: string; available?: boolean; reason?: string | null }
export interface DeliveryPlan {
  materials: DeliveryMaterialInfo[]; recipients: DeliveryRecipient[]; mail_configured: boolean; allowed_domains: string[]; max_attachment_mb?: number;
  available_kinds?: DeliveryMaterialInfo[]; selected?: string[]; attach_format?: string;
  templates?: DeliveryTemplate[]; template_id?: string | null; formats?: string[]; selected_formats?: string[];
}
export interface SyncRun {
  id: string; trigger: string; actor: string | null; status: "running" | "ok" | "partial" | "unavailable" | "failed"; started_at: string; finished_at: string | null;
  checked: number; missing: number; restored: number; orphans: number; unavailable: number;
  details?: { per_kind?: Record<string, Record<string, number>>; missing?: { kind: string; name: string; dir: string }[]; orphans?: { storage: string; path: string }[];
    unavailable_backends?: Record<string, string>; suspicious?: Record<string, string>; orphan_scan_skipped?: boolean };
}
export interface SyncOverview { settings: { enabled: boolean; interval_hours: number; batch_size: number; guard_percent: number }; runs: SyncRun[]; running: SyncRun | null; last: SyncRun | null }
export interface ApiProfile { id: string; kind: ProfileKind; name: string; config: Record<string, unknown>; secret_set: boolean; is_default: boolean; virtual: boolean }
export interface RoomAdmin {
  id: string; slug: string; name: string; description: string | null; is_enabled: boolean; max_participants: number;
  has_password: boolean; transcription_enabled: boolean; record_audio: boolean; camera_allowed: boolean;
  screen_share_allowed: boolean; text_retention_days: number | null; audio_retention_days: number | null;
  protocol_instructions: string | null; history_access: HistoryAccess; acl: AclEntry[]; active_meeting_id: string | null;
  anonymize_mode: AnonymizeMode; llm_profile_id: string | null; anonymizer_profile_id: string | null;
  mute_on_join: boolean; welcome_message: string | null; moderators: AclEntry[];
  guest_access_enabled: boolean; guest_token: string | null;
  room_type: RoomType; auto_record: boolean; board_allowed: boolean; board_access?: string;
  slug_history?: string[]; lifetime?: "permanent" | "temporary"; lifecycle?: "active" | "grace_period" | "closed"; closed_at?: string | null; created_by_name?: string | null;
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
  available: boolean; heartbeat_age_s: number | null; state: string | null; action?: string; repair_id?: string; request_id?: string; step_no?: number; step_total?: number; step_name?: string;
  started_at?: number; finished_at?: number; exit_code?: number | null; result?: string; request_pending?: boolean; project?: string; by?: string;
  /** Результат прежнего запуска из веб-интерфейса, после которого уже было успешное обновление: текущим не считается. */
  stale?: boolean;
}
export interface UpdateAttempt {
  at: number; started: number; result: "ok" | "failed"; stage: string; from_version: string; to_version: string; from_commit: string; to_commit: string; source: "cli" | "web"; by: string; has_changes?: boolean;
}
export interface RemoteCommit { sha: string; date: string; subject: string }
export interface RemoteInfo {
  checked_at: number; age_s?: number; ok: boolean; error: string; branch: string; current: string; remote: string; behind: number; ahead: number;
  ff_possible: boolean; local_changes: number; current_version?: string; remote_version?: string; changelog?: string; migrations_changed: number; env_example_changed: boolean; commits: RemoteCommit[];
}
/** Итог обновления раздельно: обновление ПО · развёртывание · работоспособность · проверка интеграций (LDAP и т. п.). */
export interface UpdateOutcome {
  update: string; deployment: string; health: string; integrations: string; integration_issues: string; needs_attention: boolean;
}
/** Помощник обновлений на сервере (служба от root). problem: not_installed — не запущен; no_privileges — запущен без прав (служба прежней версии). */
export interface HelperInfo { available: boolean; privileged: boolean; uid: number | null; problem: null | "not_installed" | "no_privileges" }
export interface LocalLlmStatus {
  enabled_by_install: boolean; provider: "local" | "external" | "off"; ready: boolean; endpoint: string;
  model: { id: string; title: string; runtime: string; light: boolean; context_tokens: number; tasks: string[]; note: string; source: string; warn_input_chars: number };
  file: { file: string; size_bytes: number | null; expected_bytes: number; sha256_state: "ok" | "mismatch" | "unchecked" | "skipped"; state: "ok" | "missing" | "partial" | "bad_size" | "bad_hash" };
  runtime: { reachable: boolean; ready: boolean; detail: string };
  catalog: { id: string; title: string; runtime: string; light: boolean; source: string }[];
}
export interface RepairItem { id: string; title: string; meaning: string; fix: string; kind: "helper" | "backend" | "manual"; fixable: boolean; command?: string }
export interface RepairsInfo {
  items: RepairItem[]; checked_at: number | null; age_s?: number | null; helper: HelperInfo; busy: boolean;
  current: { repair_id: string | null; state: string | null; result: string | null; finished_at: number | null } | null;
}
export interface LegacyLdap {
  present: boolean; migrated: boolean; active: boolean; needs_import: boolean; uris: string[]; base_dn: string; bind_dn: string; password_set: boolean;
  ca_file: string | null; ca_file_readable: boolean; admin_group_dn: string | null; access_group_dn: string | null; last_error: string | null;
}
export interface UpdatesOverview {
  installed: { version: string; commit: string; built_at: string }; updater: UpdaterState; remote: RemoteInfo | null; active_meetings: number;
  outcome: UpdateOutcome | null; helper: HelperInfo;
  can_update: boolean; reasons: string[]; up_to_date: boolean; commands: Record<string, string>;
  history: UpdateAttempt[]; last_success: UpdateAttempt | null; changes?: UpdateChanges | null;
}
export interface AutoUpdateLast { at: number; result: "updated" | "no_update" | "failed" | "deferred" | "skipped" | string; from_version?: string | null; to_version?: string | null; duration_s?: number | null; error?: string; detail?: string }
export interface AutoUpdateInfo {
  settings: { enabled: boolean; time: string; window_hours: number }; phase: "idle" | "checking" | "waiting" | "running" | string; deferred: string | null;
  from_version: string | null; to_version: string | null; last: AutoUpdateLast | null; next_run_at: string | null; installed: string;
}
export interface UpdateChanges {
  installed: string | null; available: string | null; versions: string[]; empty: boolean; saved_at?: number; by?: string;
  groups: { id: string; title: string; items: { version: string; text: string; alerts: string[] }[] }[];
  important: { version: string; kinds: string[]; text: string }[]; facts: { kind: string; text: string }[]; alert_text: Record<string, string>;
}
export interface UpdateLog {
  offset: number; size: number; text: string; reset: boolean; state: string | null; step_no: number | null; step_total: number | null; step_name: string | null;
  exit_code: number | null; result: string | null; finished_at: number | null; available: boolean;
  action?: string | null; repair_id?: string | null; outcome?: UpdateOutcome | null;
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

function errorFrom(res: Response, data: { detail?: unknown } | null): ApiError {
  const d = data?.detail as { code?: string; message?: string } | string | undefined;
  const code = typeof d === "object" && d ? d.code ?? "error" : "error";
  const message = typeof d === "object" && d ? d.message : typeof d === "string" ? d : `Ошибка ${res.status}`;
  return new ApiError(res.status, code, message ?? `Ошибка ${res.status}`, Number(res.headers.get("Retry-After")) || undefined);
}

function authHeaders(method: string): Record<string, string> {
  const h: Record<string, string> = {};
  if (guestToken) h["X-Guest-Token"] = guestToken;
  else if (method !== "GET" && csrfToken) h["X-CSRF-Token"] = csrfToken;
  return h;
}

/** Загрузка картинки PUT-ом (аватарка): тело — сами байты. */
async function putBlob<T>(path: string, blob: Blob): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { method: "PUT", headers: { ...authHeaders("PUT"), "Content-Type": blob.type || "application/octet-stream", Accept: "application/json" }, credentials: "same-origin", body: blob });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером");
  }
  const data = await res.json().catch(() => null);
  if (res.status === 413) throw new ApiError(413, "too_large", typeof data?.detail === "string" ? data.detail : "Файл слишком большой");
  if (!res.ok) throw errorFrom(res, data);
  return data as T;
}

/** Загрузка файла «как есть» (тело запроса — байты). Ошибки сервера (тип, размер, хранилище) приходят понятным текстом. */
async function uploadBytes<T>(path: string, file: File): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { method: "POST", headers: { ...authHeaders("POST"), "Content-Type": "application/octet-stream", Accept: "application/json" }, credentials: "same-origin", body: file });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером");
  }
  const data = await res.json().catch(() => null);
  if (res.status === 413) throw new ApiError(413, "too_large", typeof data?.detail === "string" ? data.detail : "Файл слишком большой");
  if (!res.ok) throw errorFrom(res, data);
  return data as T;
}

async function fetchBlob(path: string): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { headers: authHeaders("GET"), credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером");
  }
  if (!res.ok) throw errorFrom(res, await res.json().catch(() => null));
  return res.blob();
}

export type ExportFormat = "md" | "txt" | "docx" | "pdf" | "html";

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
  /** Смена пароля локального администратора (первичный/сброшенный пароль меняется при первом входе). */
  changePassword: (current_password: string, new_password: string) => request<void>("POST", "/auth/change-password", { current_password, new_password }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<Me>("GET", "/auth/me"),
  rooms: () => request<Room[]>("GET", "/rooms"),
  resolveRoom: (ref: string) => request<RoomRef>("GET", `/rooms/resolve/${encodeURIComponent(ref)}`),
  temporaryPolicy: () => request<TempRoomPolicy>("GET", "/rooms/temporary/policy"),
  createTemporaryRoom: (name: string) => request<Room>("POST", "/rooms/temporary", { name }),
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
  sendChat: (id: string, text: string, attachments: string[] = []) => request<ChatMessage>("POST", `/meetings/${id}/chat`, attachments.length ? { text, attachments } : { text }),
  /** Загрузка файла для сообщения: тело — сами байты, имя — в параметре (сервер очищает его и выбирает путь сам). */
  uploadAttachment: (id: string, file: File) => uploadBytes<ChatAttachment>(`/meetings/${id}/chat/attachments?name=${encodeURIComponent(file.name || "file")}`, file),
  discardAttachment: (id: string, attId: string) => request<void>("DELETE", `/meetings/${id}/chat/attachments/${attId}`),
  /** Файл вложения как Blob (с заголовками сессии — работает и для гостя; картинки показываются через object URL). */
  attachmentBlob: (id: string, attId: string, download = false) => fetchBlob(`/meetings/${id}/chat/attachments/${attId}${download ? "?download=true" : ""}`),
  chatTextUrl: (id: string) => `/api/v1/meetings/${id}/chat.txt`,
  whiteboard: (id: string) => request<WhiteboardState>("GET", `/meetings/${id}/whiteboard`),
  whiteboardPatch: (id: string, body: { patch: unknown; checksum?: string | null; client_id: string }) =>
    request<{ seq: number }>("POST", `/meetings/${id}/whiteboard/patch`, body),
  whiteboardSave: (id: string, xml: string, seq: number) =>
    request<{ saved: boolean; seq: number; shapes: number; used: boolean }>("PUT", `/meetings/${id}/whiteboard`, { xml, seq }),
  typing: (id: string, typing: boolean) => request<void>("POST", `/meetings/${id}/chat/typing`, { typing }),
  hand: (id: string, raised: boolean, identity?: string) => request<{ raised: boolean; queue: HandInfo[] }>("POST", `/meetings/${id}/hand`, { raised, ...(identity ? { identity } : {}) }),
  hands: (id: string) => request<{ hands: HandInfo[] }>("GET", `/meetings/${id}/hands`),
  meetingAvatars: (id: string) => request<Record<string, string>>("GET", `/meetings/${id}/avatars`),
  participantCard: (id: string, identity: string) => request<ParticipantCard>("GET", `/meetings/${id}/participants/${encodeURIComponent(identity)}/card`),
  profile: () => request<Profile>("GET", "/profile"),
  refreshProfile: () => request<Profile>("POST", "/profile/refresh"),
  uploadAvatar: (blob: Blob) => putBlob<Profile>("/profile/avatar", blob),
  deleteAvatar: () => request<Profile>("DELETE", "/profile/avatar"),
  whiteboardFileUrl: (id: string) => `/api/v1/meetings/${id}/whiteboard.drawio`,
  setRecording: (meetingId: string, enabled: boolean) => request<{ enabled: boolean }>("POST", `/meetings/${meetingId}/recording`, { enabled }),
  /** «Остановить / возобновить транскрибацию»: звонок и запись аудио не затрагиваются. */
  setTranscription: (meetingId: string, enabled: boolean) => request<{ enabled: boolean }>("POST", `/meetings/${meetingId}/transcription`, { enabled }),
  floor: (meetingId: string) => request<FloorState>("GET", `/meetings/${meetingId}/floor`),
  /** «Дать слово» / «Забрать слово»: временное право до конца встречи. */
  setFloor: (meetingId: string, identity: string, granted: boolean) => request<{ identity: string; granted: boolean }>("POST", `/meetings/${meetingId}/moderation/floor`, { identity, granted }),
  kick: (meetingId: string, identity: string) => request<{ identity: string; removed: boolean }>("POST", `/meetings/${meetingId}/moderation/kick`, { identity }),

  /** «Настройки комнаты» для руководителя. */
  manage: {
    get: (roomId: string) => request<RoomManage>("GET", `/rooms/${roomId}/manage`),
    patch: (roomId: string, body: Record<string, unknown>) => request<RoomManage>("PATCH", `/rooms/${roomId}/manage`, body),
    guestLink: (roomId: string, action: "rotate" | "revoke") => request<RoomManage>("POST", `/rooms/${roomId}/manage/guest-link/${action}`),
    search: (roomId: string, kind: "group" | "user", q: string) => request<DirHit[]>("GET", `/rooms/${roomId}/manage/directory?kind=${kind}&q=${encodeURIComponent(q)}`),
    /** Кому уйдёт рассылка по этим (даже несохранённым) настройкам: адреса из каталога, у кого адреса нет, что запрещено политикой. */
    meetingSettings: (meetingId: string) => request<MeetingSettings>("GET", `/meetings/${meetingId}/settings`),
    saveMeetingSettings: (meetingId: string, body: { delivery?: MailDeliverySpec | null; llm?: LlmChoice | null; llm_summary?: LlmChoice | null }) => request<MeetingSettings>("PUT", `/meetings/${meetingId}/settings`, body),
    phone: (roomId: string) => request<PhoneState>("GET", `/rooms/${roomId}/phone`),
    phoneCall: (roomId: string, body: { number?: string; contact?: number }) => request<{ guest_id: string; identity: string; display_name: string; profile: string }>("POST", `/rooms/${roomId}/phone/call`, body),
    phoneHangup: (roomId: string, guestId: string) => request<{ ok: boolean }>("POST", `/rooms/${roomId}/phone/hangup`, { guest_id: guestId }),
    deliveryPreview: (roomId: string, spec: MailDeliverySpec) =>
      request<{ recipients: DeliveryRecipient[]; participants_by_meeting: boolean; mail_configured: boolean; allowed_domains: string[]; materials: DeliveryMaterialInfo[] }>("POST", `/rooms/${roomId}/manage/delivery-preview`, { mail_delivery: spec }),
  },
  /** Ручная отправка материалов завершённой встречи (руководитель комнаты / администратор). */
  delivery: {
    preview: (meetingId: string) => request<DeliveryPlan>("GET", `/meetings/${meetingId}/delivery`),
    send: (meetingId: string, kinds: string[], emails: string[], extra: { template_id?: string | null; subject?: string; body?: string; formats?: string[] } = {}) =>
      request<{ batch_id: string; queued: number; skipped: { name: string; email: string; reason: string }[]; kinds: string[]; unavailable: string[] }>("POST", `/meetings/${meetingId}/delivery/send`, { kinds, emails, ...extra }),
    log: (meetingId: string) => request<DeliveryLogRow[]>("GET", `/meetings/${meetingId}/delivery/log`),
  },
  mailTemplateNames: () => request<{ items: { id: string; name: string; is_default: boolean }[]; formats: string[] }>("GET", "/mail-templates"),
  mailTemplates: {
    list: () => request<{ items: MailTemplate[]; variables: { name: string; describe: string }[]; materials: { kind: string; label: string }[] }>("GET", "/admin/mail/templates"),
    create: (b: Partial<MailTemplate>) => request<MailTemplate>("POST", "/admin/mail/templates", b),
    update: (id: string, b: Partial<MailTemplate>) => request<MailTemplate>("PUT", `/admin/mail/templates/${id}`, b),
    remove: (id: string) => request<void>("DELETE", `/admin/mail/templates/${id}`),
  },

  conversationMap: (meetingId: string) => request<MapState>("GET", `/meetings/${meetingId}/map`),
  createMap: (meetingId: string) => request<{ status: string }>("POST", `/meetings/${meetingId}/map`),
  editMapTopic: (meetingId: string, topicId: string, patch: MapTopicEdit) => request<MapState>("PATCH", `/meetings/${meetingId}/map/topics/${encodeURIComponent(topicId)}`, patch),
  logMapExport: (meetingId: string) => request<void>("POST", `/meetings/${meetingId}/map/export`),

  protocols: (meetingId: string) => request<ProtocolItem[]>("GET", `/meetings/${meetingId}/protocols`),
  protocol: (meetingId: string, id: string) => request<ProtocolItem>("GET", `/meetings/${meetingId}/protocols/${id}`),
  defaultInstruction: (meetingId: string, kind: ProtocolKind, llm = "") =>
    request<{ kind: string; instruction: string; plan?: ProtocolPlan; can_override?: boolean; llm_choices?: LlmChoiceOnce[] }>(
      "GET", `/meetings/${meetingId}/protocols/default-instruction?kind=${kind}${llm ? `&llm=${encodeURIComponent(llm)}` : ""}`),
  createProtocol: (meetingId: string, kind: ProtocolKind, instruction: string, llmOnce = "") =>
    request<{ protocol_id: string }>("POST", `/meetings/${meetingId}/protocols`, { kind, instruction, ...(llmOnce ? { llm_once: llmOnce } : {}) }),
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
    rooms: (includeClosed = false) => request<RoomAdmin[]>("GET", `/admin/rooms${includeClosed ? "?include_closed=true" : ""}`),
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
    updateChanges: (from: string, to: string) => request<UpdateChanges>("GET", `/admin/updates/changes?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
    updatesRun: (body: { confirm: true; force_build: boolean; pull: boolean }) => request<{ request_id: string }>("POST", "/admin/updates/run", body),
    updatesLog: (offset: number) => request<UpdateLog>("GET", `/admin/updates/log?offset=${offset}`),
    updateComponents: (refresh = false) => request<ComponentsInfo>("GET", `/admin/updates/components${refresh ? "?refresh=true" : ""}`),

    profiles: (kind: ProfileKind) => request<ApiProfile[]>("GET", `/admin/api-profiles?kind=${kind}`),
    createProfile: (body: { kind: ProfileKind; name: string; config: Record<string, unknown>; secret?: string; make_default?: boolean }) => request<ApiProfile>("POST", "/admin/api-profiles", body),
    updateProfile: (id: string, body: { name?: string; config?: Record<string, unknown>; secret?: string | null }) => request<ApiProfile>("PATCH", `/admin/api-profiles/${id}`, body),
    deleteProfile: (id: string) => request<void>("DELETE", `/admin/api-profiles/${id}`),
    setDefaultProfile: (kind: ProfileKind, profileId: string) => request<{ ok: boolean }>("PUT", "/admin/api-profiles/default", { kind, profile_id: profileId }),
    testProfile: (kind: ProfileKind, id: string) => request<TestResult>("POST", `/admin/api-profiles/${id}/test?kind=${kind}`),
    ldapProfiles: () => request<{ items: LdapProfile[]; env: { configured: boolean; uris: string[]; base_dn: string }; errors: Record<string, string>; active: boolean; legacy: LegacyLdap; boot_errors: Record<string, string> }>("GET", "/admin/ldap-profiles"),
    ldapLegacyImport: () => request<{ profiles: string[]; ca_added: number; groups_added: number }>("POST", "/admin/ldap-legacy/import"),
    localLlm: () => request<LocalLlmStatus>("GET", "/admin/llm/local"),
    sipStatus: () => request<SipStatus>("GET", "/admin/sip/status"),
    sipProfiles: () => request<{ items: SipProfile[] }>("GET", "/admin/sip/profiles"),
    createSip: (body: Record<string, unknown>) => request<SipProfile>("POST", "/admin/sip/profiles", body),
    updateSip: (id: string, body: Record<string, unknown>) => request<SipProfile>("PATCH", `/admin/sip/profiles/${id}`, body),
    deleteSip: (id: string) => request<void>("DELETE", `/admin/sip/profiles/${id}`),
    syncSip: (id: string) => request<{ ok: boolean | null; message: string }>("POST", `/admin/sip/profiles/${id}/sync`),
    checkSip: (id: string) => request<DiagResult>("POST", `/admin/sip/profiles/${id}/check`),
    testCallSip: (id: string, number: string) => request<{ ok: boolean; message: string; ms: number; sip_status?: number | null }>("POST", `/admin/sip/profiles/${id}/test-call`, { number }),
    localLlmTest: () => request<{ ok: boolean; message: string; ms: number }>("POST", "/admin/llm/local/test"),
    repairs: () => request<RepairsInfo>("GET", "/admin/updates/repairs"),
    repairsScan: () => request<{ request_id: string }>("POST", "/admin/updates/repairs/scan"),
    repairFix: (id: string) => request<{ request_id?: string; done?: boolean }>("POST", `/admin/updates/repairs/${encodeURIComponent(id)}/fix`),
    createLdap: (body: Record<string, unknown>) => request<LdapProfile>("POST", "/admin/ldap-profiles", body),
    updateLdap: (id: string, body: Record<string, unknown>) => request<LdapProfile>("PATCH", `/admin/ldap-profiles/${id}`, body),
    deleteLdap: (id: string) => request<void>("DELETE", `/admin/ldap-profiles/${id}`),
    moveLdap: (id: string, direction: -1 | 1) => request<{ items: LdapProfile[] }>("POST", `/admin/ldap-profiles/${id}/move`, { direction }),
    testLdap: (id: string) => request<DiagResult>("POST", `/admin/ldap-profiles/${id}/test`),
    caList: () => request<{ items: CaCert[] }>("GET", "/admin/ca"),
    caInspect: (body: { pem?: string; data_base64?: string }) => request<{ items: CaInfo[] }>("POST", "/admin/ca/inspect", body),
    caAdd: (body: { pem?: string; data_base64?: string; label?: string; confirm_non_ca?: boolean }) => request<{ added: CaInfo[]; already_present: CaInfo[] }>("POST", "/admin/ca", body),
    caDelete: (id: string) => request<void>("DELETE", `/admin/ca/${id}`),
    localAdmin: () => request<LocalAdminInfo>("GET", "/admin/local-admin"),
    autoUpdate: () => request<AutoUpdateInfo>("GET", "/admin/updates/auto"),
    autoUpdateRun: () => request<{ started: boolean }>("POST", "/admin/updates/auto/run"),
    effectiveModels: () => request<EffectiveModels>("GET", "/admin/llm/effective"),
    modelStats: (f: { days?: number; kind?: string; where?: string; archived?: boolean } = {}) =>
      request<{ documents: number; models: ModelStat[]; archived_hidden: number }>("GET", `/admin/llm/stats?days=${f.days ?? 0}&kind=${f.kind ?? ""}&where=${f.where ?? ""}&archived=${f.archived ? "true" : "false"}`),
    llmChoices: () => request<LlmChoices>("GET", "/admin/llm/choices"),
    llmDiagnose: (target: string) => request<LlmDiagnose>("POST", "/admin/llm/diagnose", { target }),
    accessStatus: () => request<AccessStatus>("GET", "/admin/access/status"),
    accessCheckUser: (login: string) => request<AccessCheck>("POST", "/admin/access/check-user", { login }),
    setupStatus: () => request<SetupStatus>("GET", "/admin/setup/status"),
    setupComplete: (skipped: string[]) => request<{ completed: boolean }>("POST", "/admin/setup/complete", { skipped }),
    mailProfiles: () => request<{ items: MailProfile[] }>("GET", "/admin/mail/profiles"),
    createMail: (body: Record<string, unknown>) => request<MailProfile>("POST", "/admin/mail/profiles", body),
    updateMail: (id: string, body: Record<string, unknown>) => request<MailProfile>("PATCH", `/admin/mail/profiles/${id}`, body),
    deleteMail: (id: string) => request<void>("DELETE", `/admin/mail/profiles/${id}`),
    activateMail: (id: string) => request<MailProfile>("POST", `/admin/mail/profiles/${id}/activate`),
    checkMail: (id: string) => request<DiagResult>("POST", `/admin/mail/profiles/${id}/check`),
    testMail: (id: string, to: string) => request<{ ok: boolean; message: string }>("POST", `/admin/mail/profiles/${id}/test-send`, { to }),
    mailMessages: (state = "", q = "", offset = 0) => request<{ items: MailMessageRow[]; counts: Record<string, number> }>("GET", `/admin/mail/messages?limit=100&offset=${offset}&state=${state}&q=${encodeURIComponent(q)}`),
    retryMail: (id: string) => request<MailMessageRow>("POST", `/admin/mail/messages/${id}/retry`),
    syncOverview: () => request<SyncOverview>("GET", "/admin/storage-sync"),
    syncRun: (force = false) => request<{ run_id: string }>("POST", "/admin/storage-sync/run", { force }),
    syncDetail: (id: string) => request<SyncRun>("GET", `/admin/storage-sync/runs/${id}`),
    storages: () => request<{ items: StorageProfile[]; folders: string[] }>("GET", "/admin/storages"),
    createStorage: (body: { name: string; kind: "local" | "smb"; config: Record<string, string>; secret?: string }) => request<StorageProfile>("POST", "/admin/storages", body),
    updateStorage: (id: string, body: { name?: string; config?: Record<string, string>; secret?: string | null }) => request<StorageProfile>("PATCH", `/admin/storages/${id}`, body),
    deleteStorage: (id: string) => request<void>("DELETE", `/admin/storages/${id}`),
    testStorage: (id: string) => request<TestResult>("POST", `/admin/storages/${id}/test`),
    retryExports: () => request<{ exported: number; still_failed: number }>("POST", "/admin/recordings/retry-exports"),
    runRetention: () => request<Record<string, number>>("POST", "/admin/retention/run"),
    clientDiagnostics: () => request<{ events: ClientEventRow[]; metrics: ClientMetricRow[]; lifecycle: ClientEventRow[] }>("GET", "/admin/client-diagnostics"),
    diagnosticsReport: () => request<DiagnosticsReport>("GET", "/admin/diagnostics/report"),
    asrModels: () => request<AsrModels>("GET", "/admin/asr/models"),
    setAsrModel: (modelId: string) => request<{ ok: boolean; desired: string; note: string }>("PUT", "/admin/asr/active", { model_id: modelId }),
    bitrixLookup: (email: string) => request<BitrixLookup>("POST", "/admin/bitrix24/lookup", { email }),
    bitrixSync: () => request<BitrixSync>("POST", "/admin/bitrix24/sync"),
    asrTest: (modelId?: string, force = false) => request<AsrTestResult>("POST", "/admin/asr/test", { model_id: modelId, force }),
    asrCompare: (force = false) => request<AsrCompare>("POST", "/admin/asr/compare", { force }),
  },
};
