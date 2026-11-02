import type { IconName } from "./components/Icons";

/** Пиктограмма каждого раздела меню администрирования (ключ — id раздела в AdminPage). У каждого раздела своя, без повторов: проверяет adminIcons.test.ts. */
export const ADMIN_ICONS: Record<string, IconName> = {
  system: "dashboard", system_tech: "cpu", performance: "gauge", clients: "devices",
  meetings: "calendar", recordings: "record", protocol: "transcript", templates: "layout",
  rooms: "door", users: "users", login_access: "login", access: "crown", profile_policy: "userEdit",
  ldap: "sitemap", ca: "certificate", mail: "mail", asr: "noise", llm: "sparkle", anon: "eyeOff", storages: "folder", outlook: "at",
  sip: "phone", sip_policy: "dialpad", video: "video", live: "broadcast", bitrix24: "building", public_api: "code",
  desktop_signing: "signature",
  auth_security: "key", security_events: "alert", security_blocks: "ban", login_protection: "shield", captcha: "robot", waf: "wall",
  security_log: "history", ip_access: "listCheck", net_trust: "route", crypto: "lock", audit: "clipboard",
  storage: "file", audio_storage: "volume", chats: "chat", chat_files: "attach", storage_sync: "checks", restore: "retry",
  journal: "list", journal_settings: "database", mail_log: "inbox",
  network: "globe", updates: "download", config_backup: "archive", general: "gear", site: "palette", privacy: "cookie",
  screen: "screen", mail_policy: "send", mail_templates: "mailOpen",
};
