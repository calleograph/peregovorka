// Настройки приложения (пользователь) поверх политик (администратор). Политика, заданная явно, сильнее и блокирует поле в интерфейсе.
import { execFile } from "node:child_process";
import { readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mergePolicy, parseRegExport, parseRegQuery, policyFromValues, type Autostart, type CloseAction, type Policy, type ProxyMode, type UpdateMode } from "../common/policy";
import { readJson, writeJson } from "./store";

export interface Settings {
  closeAction: CloseAction;
  autostart: Autostart;
  notifications: { enabled: boolean; sound: boolean; hideContent: boolean; dndUntil: number };
  proxy: { mode: ProxyMode; rules: string; bypass: string; pac: string };
  updateMode: UpdateMode;
  theme: "system" | "light" | "dark";        // оформление окон приложения (страницы серверов следуют ему же, если в профиле выбрано «как в системе»)
  activeOrg: string | null;
  firstRunDone: boolean;
}

export const DEFAULTS: Settings = {
  closeAction: "tray",
  autostart: "off",
  notifications: { enabled: true, sound: true, hideContent: false, dndUntil: 0 },
  proxy: { mode: "system", rules: "", bypass: "", pac: "" },
  updateMode: "notify",                      // проверять и сообщать; загрузку начинает человек (или политика UpdateMode=auto)
  theme: "system",
  activeOrg: null,
  firstRunDone: false,
};

/** Какие поля задал администратор (интерфейс показывает их недоступными). */
export function lockedFields(p: Policy): string[] {
  const out: string[] = [];
  if (p.closeAction) out.push("closeAction");
  if (p.autostart) out.push("autostart");
  if (p.updateMode || p.updateUrl) out.push("updateMode");
  if (p.notifications !== undefined) out.push("notifications.enabled");
  if (p.hideContent !== undefined) out.push("notifications.hideContent");
  if (p.proxy) out.push("proxy");
  if (p.allowAddServers === false) out.push("addServers");
  return out;
}

export function effective(s: Settings, p: Policy): Settings {
  return {
    ...s,
    closeAction: p.closeAction ?? s.closeAction,
    autostart: p.autostart ?? s.autostart,
    updateMode: p.updateMode ?? s.updateMode,
    notifications: { ...s.notifications, enabled: p.notifications ?? s.notifications.enabled, hideContent: p.hideContent ?? s.notifications.hideContent },
    proxy: p.proxy ? { mode: p.proxy.mode, rules: p.proxy.rules ?? "", bypass: p.proxy.bypass ?? "", pac: p.proxy.pac ?? "" } : s.proxy,
  };
}

export class SettingsStore {
  private file: string;
  private s: Settings;
  constructor(dir: string) {
    this.file = join(dir, "settings.json");
    const raw = readJson<Partial<Settings>>(this.file, {});
    this.s = { ...DEFAULTS, ...raw, notifications: { ...DEFAULTS.notifications, ...(raw.notifications ?? {}) }, proxy: { ...DEFAULTS.proxy, ...(raw.proxy ?? {}) } };
  }
  get(): Settings { return this.s; }
  /** Правка от интерфейса: только известные поля и допустимые значения. */
  patch(p: Record<string, unknown>): Settings {
    const s = structuredClone(this.s);
    const one = <T extends string>(v: unknown, ok: readonly T[]) => (ok as readonly unknown[]).includes(v) ? (v as T) : undefined;
    s.closeAction = one(p.closeAction, ["tray", "quit", "ask"] as const) ?? s.closeAction;
    s.autostart = one(p.autostart, ["off", "tray", "window"] as const) ?? s.autostart;
    s.updateMode = one(p.updateMode, ["auto", "notify", "off"] as const) ?? s.updateMode;
    s.theme = one(p.theme, ["system", "light", "dark"] as const) ?? s.theme;
    const n = (p.notifications ?? {}) as Record<string, unknown>;
    for (const k of ["enabled", "sound", "hideContent"] as const) if (typeof n[k] === "boolean") s.notifications[k] = n[k] as boolean;
    if (typeof n.dndUntil === "number" && Number.isFinite(n.dndUntil) && n.dndUntil >= -1) s.notifications.dndUntil = n.dndUntil;
    const px = (p.proxy ?? {}) as Record<string, unknown>;
    s.proxy.mode = one(px.mode, ["system", "direct", "manual", "pac"] as const) ?? s.proxy.mode;
    for (const k of ["rules", "bypass", "pac"] as const) if (typeof px[k] === "string") s.proxy[k] = (px[k] as string).slice(0, 1000).trim();
    if (s.proxy.mode === "pac" && s.proxy.pac && !/^https?:\/\//.test(s.proxy.pac)) s.proxy.pac = "";
    if (p.activeOrg === null || typeof p.activeOrg === "string") s.activeOrg = p.activeOrg as string | null;
    if (typeof p.firstRunDone === "boolean") s.firstRunDone = p.firstRunDone;
    this.s = s;
    writeJson(this.file, s);
    return s;
  }
}

/** Ключ реестра как текст `reg export` (UTF-16 → строка; пусто, если ключа нет). */
function reg(key: string): Promise<string> {
  const file = join(tmpdir(), `peregovorka-policy-${process.pid}-${Math.random().toString(36).slice(2)}.reg`);
  return new Promise((res) => execFile("reg.exe", ["export", key, file, "/y"], { windowsHide: true, timeout: 5000 }, (e) => {
    let text = "";
    if (!e) try { text = readFileSync(file).toString("utf16le"); } catch { /* нет файла — нет политики */ }
    rmSync(file, { force: true });
    res(text);
  }));
}

/** Политики Windows (HKLM сильнее HKCU). Вне Windows — файл из PEREGOVORKA_POLICY_FILE (только для испытаний). */
export async function loadPolicy(): Promise<Policy> {
  if (process.platform === "win32") {
    const [m, u] = await Promise.all([reg("HKLM\\SOFTWARE\\Policies\\Peregovorka"), reg("HKCU\\SOFTWARE\\Policies\\Peregovorka")]);
    return mergePolicy(policyFromValues(parseRegExport(m)), policyFromValues(parseRegExport(u)));
  }
  const f = process.env.PEREGOVORKA_POLICY_FILE;
  if (!f) return { servers: [] };
  try {
    const raw = JSON.parse(readFileSync(f, "utf8")) as Record<string, unknown>;
    const lines = Object.entries(raw).map(([k, v]) => `    ${k}    ${typeof v === "number" ? "REG_DWORD" : "REG_SZ"}    ${typeof v === "number" ? "0x" + v.toString(16) : String(v)}`);
    return policyFromValues(parseRegQuery(lines.join("\n")));
  } catch {
    return { servers: [] };
  }
}
