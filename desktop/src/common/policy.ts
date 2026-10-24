// Корпоративные политики: ключ реестра HKLM (затем HKCU)\SOFTWARE\Policies\Peregovorka — стандартное место для групповых политик (шаблон ADMX в
// desktop/installer/admx) и для свойств MSI. Значение, заданное администратором, сильнее пользовательской настройки и в интерфейсе показывается
// как «задано администратором». Разбор вывода `reg query` — чистая функция (проверяется тестами без Windows).
import { normalizeServerUrl } from "./urls";

export type Autostart = "off" | "tray" | "window";
export type CloseAction = "tray" | "quit" | "ask";
export type UpdateMode = "auto" | "notify" | "off";
export type ProxyMode = "system" | "direct" | "manual" | "pac";

export interface Policy {
  servers: { name: string; origin: string }[];
  allowAddServers?: boolean;
  allowedDomains?: string[];
  autostart?: Autostart;
  closeAction?: CloseAction;
  updateMode?: UpdateMode;
  updateUrl?: string;
  notifications?: boolean;
  hideContent?: boolean;
  proxy?: { mode: ProxyMode; rules?: string; bypass?: string; pac?: string };
  allowDevTools?: boolean;
}

type RegValue = { type: string; data: string };

/** Вывод `reg query <ключ>` → имя значения → (тип, данные). Имена без учёта регистра. */
export function parseRegQuery(out: string): Map<string, RegValue> {
  const m = new Map<string, RegValue>();
  for (const line of out.split(/\r?\n/)) {
    const r = /^\s{2,}(\S+)\s{2,}(REG_[A-Z_]+)(?:\s{2,}(.*))?$/.exec(line);
    if (r) m.set(r[1].toLowerCase(), { type: r[2], data: (r[3] ?? "").trim() });
  }
  return m;
}

/** Файл `reg export` (UTF-16, уже раскодирован) → то же, что parseRegQuery. `reg query` для этого не годится: он печатает в кодировке консоли (OEM),
 *  и кириллица в названиях организаций из групповой политики превращалась в «????». Значения hex(…) не используются политиками и пропускаются. */
export function parseRegExport(text: string): Map<string, RegValue> {
  const m = new Map<string, RegValue>();
  for (const line of text.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const r = /^"((?:[^"\\]|\\.)*)"=(.*)$/.exec(line);
    if (!r) continue;
    const name = r[1].replace(/\\(.)/g, "$1").toLowerCase();
    const v = r[2];
    const str = /^"((?:[^"\\]|\\.)*)"$/.exec(v);
    if (str) m.set(name, { type: "REG_SZ", data: str[1].replace(/\\(.)/g, "$1") });
    else if (/^dword:[0-9a-f]{8}$/i.test(v)) m.set(name, { type: "REG_DWORD", data: "0x" + v.slice(6) });
  }
  return m;
}

const pick = <T extends string>(v: string | undefined, allowed: readonly T[]): T | undefined => (allowed as readonly string[]).includes((v ?? "").toLowerCase()) ? (v!.toLowerCase() as T) : undefined;
const flag = (v: RegValue | undefined): boolean | undefined => {
  if (!v) return undefined;
  const n = v.type === "REG_DWORD" ? parseInt(v.data, 16) : parseInt(v.data, 10);
  return Number.isFinite(n) ? n !== 0 : undefined;
};
const list = (s: string) => s.split(/\\0|[;\n]/).map((x) => x.trim()).filter(Boolean);

/** Значения реестра → политика. Неверные значения игнорируются (как будто не заданы), а не ломают запуск. */
export function policyFromValues(v: Map<string, RegValue>): Policy {
  const p: Policy = { servers: [] };
  for (const item of list(v.get("servers")?.data ?? "")) {
    const [a, b] = item.includes("|") ? item.split("|", 2) : ["", item];
    const n = normalizeServerUrl(b.trim());
    if ("origin" in n && !p.servers.some((s) => s.origin === n.origin)) p.servers.push({ name: a.trim().slice(0, 60) || new URL(n.origin).hostname, origin: n.origin });
  }
  p.allowAddServers = flag(v.get("allowaddservers"));
  const doms = list(v.get("alloweddomains")?.data ?? "");
  if (doms.length) p.allowedDomains = doms;
  p.autostart = pick(v.get("autostart")?.data, ["off", "tray", "window"] as const);
  p.closeAction = pick(v.get("closeaction")?.data, ["tray", "quit", "ask"] as const);
  p.updateMode = pick(v.get("updatemode")?.data, ["auto", "notify", "off"] as const);
  const uu = v.get("updateurl")?.data;
  if (uu && /^https:\/\/[^\s]+$/.test(uu)) p.updateUrl = uu;
  p.notifications = flag(v.get("notifications"));
  p.hideContent = flag(v.get("hidenotificationcontent"));
  const pm = pick(v.get("proxymode")?.data, ["system", "direct", "manual", "pac"] as const);
  if (pm) p.proxy = { mode: pm, rules: v.get("proxyserver")?.data || undefined, bypass: v.get("proxybypass")?.data || undefined, pac: v.get("proxypac")?.data || undefined };
  p.allowDevTools = flag(v.get("allowdevtools"));
  for (const k of Object.keys(p) as (keyof Policy)[]) if (p[k] === undefined) delete p[k];
  return p;
}

/** HKLM сильнее HKCU (машинная политика администратора важнее пользовательской); списки серверов объединяются. */
export function mergePolicy(machine: Policy, user: Policy): Policy {
  const servers = [...machine.servers];
  for (const s of user.servers) if (!servers.some((x) => x.origin === s.origin)) servers.push(s);
  return { ...user, ...machine, servers };
}
