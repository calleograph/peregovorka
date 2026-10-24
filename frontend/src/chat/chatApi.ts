// Клиент API корпоративных чатов (/api/v1/chats). Типы повторяют ответы backend (services/chats.py, api/chats.py).
import { http } from "../api";

export interface ChatFileInfo { id: string; name: string; mime: string; size: number; kind: "image" | "file"; missing?: boolean }
export interface ChatMsg {
  id: number; chat_id: string; author_id: string | null; author_name: string; kind: "text" | "system";
  text: string; created_at: string; edited_at: string | null; deleted: boolean; client_id: string | null;
  forwarded: { name: string; at: string } | null;
  reply_to: { id: number; author_name: string; text: string; deleted: boolean } | null;
  files: ChatFileInfo[];
  /** Реакции участников: значок, число, кто поставил (до 50). */
  reactions?: { emoji: string; count: number; user_ids: string[]; names: string[] }[];
  /** Только на клиенте: сообщение ещё отправляется (или не отправилось) — показывается сразу, без ожидания сервера. */
  pending?: "sending" | "failed";
}
export interface ChatPeer { id: string; name: string; department: string | null; active: boolean; online?: boolean }
export interface ChatItem {
  id: string; kind: "direct" | "group"; title: string; avatar_url: string | null; peer: ChatPeer | null; members: number; role: "admin" | "member";
  last: { id: number; author_id: string | null; author_name: string; kind: string; text: string; deleted: boolean; at: string } | null;
  last_at: string; unread: number; last_read_id: number; muted: boolean; pinned: boolean; pinned_at: string | null; archived: boolean; folder_id: string | null; hidden: boolean;
}
export interface ChatMember { id: string; name: string; department: string | null; title: string | null; email: string | null; role: "admin" | "member"; avatar_url: string | null; active: boolean; online?: boolean; watch?: boolean }
export interface ChatInfo {
  id: string; kind: "direct" | "group"; title: string | null; avatar_url: string | null; created_at: string; my_role: "admin" | "member"; members: ChatMember[];
  prefs: { muted: boolean; pinned: boolean; archived: boolean; folder_id: string | null };
}
export interface ChatFolder { id: string; name: string; position: number }
export interface ChatPrefs { presence: boolean; sound: boolean }
export interface ChatConfig {
  enabled: boolean; max_message_chars: number; max_group_members: number; presence: boolean;
  files: { enabled: boolean; max_size_mb: number; max_files: number; types: string[] };
  dictation: { enabled: boolean; available: boolean; max_seconds: number };
  prefs: ChatPrefs; me: { id: string; name: string };
  /** Допустимые реакции; первые шесть — быстрая панель. */
  reactions: string[];
}
export interface Person { id: string | null; ref: string | null; name: string; login: string | null; department?: string | null; title?: string | null; avatar_url?: string | null; source: "local" | "directory" }
export interface PeopleResult { users: Person[]; groups: { ref: string; name: string; description: string | null }[]; directory: "ok" | "not_configured" | "unavailable" | null }
export interface Page { messages: ChatMsg[]; has_more_before: boolean | null; has_more_after: boolean; last_read_id?: number; reads?: Record<string, number> }
export interface SearchHit { id: number; author_name: string; created_at: string; text: string; chat_id?: string }
export interface SearchResult { items: SearchHit[]; total: number; more: boolean; terms: string[] }
export interface Receipt { user_id: string; name: string; read: boolean }

const C = "/chats";
const q = (o: Record<string, string | number | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const chatApi = {
  config: () => http.request<ChatConfig>("GET", `${C}/config`),
  prefs: (p: Partial<ChatPrefs>) => http.request<ChatPrefs>("PUT", `${C}/prefs`, p),
  list: () => http.request<{ items: ChatItem[]; unread: number; server_time: string }>("GET", C),
  unread: () => http.request<{ unread: number; enabled: boolean }>("GET", `${C}/unread`),
  people: (text: string) => http.request<PeopleResult>("GET", `${C}/people${q({ q: text })}`),
  groupMembers: (ref: string) => http.request<{ users: Person[] }>("POST", `${C}/groups/members-of`, { ref }),
  direct: (p: Pick<Person, "id" | "ref" | "login">) => http.request<{ id: string; created: boolean }>("POST", `${C}/direct`, p),
  createGroup: (title: string, members: Pick<Person, "id" | "ref" | "login">[]) => http.request<{ id: string }>("POST", `${C}/group`, { title, members }),
  info: (id: string) => http.request<ChatInfo>("GET", `${C}/${id}`),
  rename: (id: string, title: string) => http.request<{ ok: true }>("PATCH", `${C}/${id}`, { title }),
  setAvatar: (id: string, blob: Blob) => http.putBlob<{ avatar_url: string }>(`${C}/${id}/avatar`, blob),
  dropAvatar: (id: string) => http.request<{ ok: true }>("DELETE", `${C}/${id}/avatar`),
  addMembers: (id: string, members: Pick<Person, "id" | "ref" | "login">[]) => http.request<{ added: number }>("POST", `${C}/${id}/members`, { members }),
  removeMember: (id: string, userId: string) => http.request<{ ok: true }>("DELETE", `${C}/${id}/members/${userId}`),
  setRole: (id: string, userId: string, role: "admin" | "member") => http.request<{ ok: true }>("PATCH", `${C}/${id}/members/${userId}`, { role }),
  setMine: (id: string, p: Partial<{ muted: boolean; pinned: boolean; archived: boolean; folder_id: string | null }>) => http.request<{ ok: true }>("PATCH", `${C}/${id}/me`, p),
  clear: (id: string) => http.request<{ ok: true }>("POST", `${C}/${id}/clear`),
  hide: (id: string) => http.request<{ ok: true }>("DELETE", `${C}/${id}`),
  messages: (id: string, o: { before_id?: number; after_id?: number; around_id?: number; limit?: number } = {}) => http.request<Page>("GET", `${C}/${id}/messages${q(o)}`),
  send: (id: string, body: { text: string; client_id: string; reply_to?: number | null; files?: string[]; forward?: { chat_id: string; message_id: number } }) =>
    http.request<ChatMsg>("POST", `${C}/${id}/messages`, body),
  edit: (id: string, mid: number, text: string) => http.request<ChatMsg>("PATCH", `${C}/${id}/messages/${mid}`, { text }),
  remove: (id: string, mid: number) => http.request<{ ok: true }>("DELETE", `${C}/${id}/messages/${mid}`),
  react: (id: string, mid: number, emoji: string | null) => http.request<ChatMsg>("POST", `${C}/${id}/messages/${mid}/reaction`, { emoji }),
  read: (id: string, last_id: number) => http.request<{ last_read_id: number }>("POST", `${C}/${id}/read`, { last_id }),
  receipts: (id: string, mid: number) => http.request<{ delivered: boolean; items: Receipt[] }>("GET", `${C}/${id}/messages/${mid}/receipts`),
  typing: (id: string) => http.request<void>("POST", `${C}/${id}/typing`),
  search: (id: string, o: { q: string; author?: string; date_from?: string; date_to?: string; before_id?: number }) => http.request<SearchResult>("GET", `${C}/${id}/search${q(o)}`),
  searchAll: (text: string) => http.request<{ chats: ChatItem[]; messages: (SearchHit & { chat_id: string })[]; terms?: string[] }>("GET", `${C}/search/all${q({ q: text })}`),
  folders: () => http.request<{ items: ChatFolder[] }>("GET", `${C}/folders/mine`),
  createFolder: (name: string) => http.request<{ id: string; name: string }>("POST", `${C}/folders/mine`, { name }),
  renameFolder: (id: string, name: string) => http.request<{ ok: true }>("PATCH", `${C}/folders/mine/${id}`, { name }),
  deleteFolder: (id: string) => http.request<{ ok: true }>("DELETE", `${C}/folders/mine/${id}`),
  watch: (userId: string, on: boolean) => http.request<{ watch: boolean }>("POST", `${C}/presence/watch/${userId}`, { on }),
  upload: (id: string, file: File) => http.uploadBytes<ChatFileInfo>(`${C}/${id}/files?name=${encodeURIComponent(file.name || "image.png")}`, file),
  dropFile: (id: string, fid: string) => http.request<void>("DELETE", `${C}/${id}/files/${fid}`),
  fileBlob: (id: string, fid: string) => http.fetchBlob(`${C}/${id}/files/${fid}`),
  fileUrl: (id: string, fid: string, download = false) => `/api/v1${C}/${id}/files/${fid}${download ? "?download=1" : ""}`,
};

/** Идентификатор отправки: повтор того же сообщения после сбоя сети сервер узнаёт по нему и не создаёт дубль. */
export function newClientId(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, "");
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}
