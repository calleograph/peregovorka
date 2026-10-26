// Небольшие помощники интерфейса оболочки: создание элементов без innerHTML (никакого HTML из данных), пиктограммы веб-интерфейса, мост к главному процессу.
import { ICONS } from "./icons.gen";

type Child = Node | string | number | null | undefined | false | Child[];
type Props = Record<string, unknown> & { class?: string; on?: Record<string, (e: Event) => void> };

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, props: Props | null = null, ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (k === "on") for (const [ev, fn] of Object.entries(v as Record<string, (e: Event) => void>)) el.addEventListener(ev, fn);
      else if (k === "class") el.className = String(v ?? "");
      else if (k === "style" && typeof v === "object") for (const [sk, sv] of Object.entries(v as Record<string, string>)) el.style.setProperty(sk.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()), sv);
      else if (k in el && typeof v !== "string") (el as unknown as Record<string, unknown>)[k] = v;
      else if (v === true) el.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) el.setAttribute(k, String(v));
    }
  }
  add(el, children);
  return el;
}

function add(el: Node, c: Child): void {
  if (c === null || c === undefined || c === false) return;
  if (Array.isArray(c)) { for (const x of c) add(el, x); return; }
  el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
}

const NS = "http://www.w3.org/2000/svg";
const parser = new DOMParser();
/** Пиктограмма из набора веб-интерфейса (frontend/src/components/Icons.tsx): разметка собрана при сборке из исходника, не из данных пользователя. */
export function icon(name: keyof typeof ICONS | string, cls = "i"): SVGSVGElement {
  const svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: cls })) svg.setAttribute(k, v);
  const body = (ICONS as Record<string, string>)[name] ?? "";
  const doc = parser.parseFromString(`<svg xmlns="${NS}">${body}</svg>`, "image/svg+xml");
  for (const n of [...doc.documentElement.childNodes]) svg.appendChild(document.importNode(n, true));
  return svg;
}

export type OrgView = { id: string; name: string; origin: string; host: string; managed: boolean; icon: string | null; status: string; problem: { title: string; hint: string; code: string } | null;
  retryAt: number | null; user: string | null; unread: number; inCall: boolean; sharing: boolean; theme: string | null; trusted: boolean; serverVersion: string | null; trustDays: number | null };
export type State = { version: string; buildKind: string; platform: string; activeId: string | null; page: string; orgs: OrgView[]; settings: Settings; locked: string[]; canAdd: boolean;
  allowedDomains: string[]; update: { status: string; version?: string; progress?: number; error?: string; checkedAt?: number; notes?: string; installOnQuit?: boolean; latest?: string; manualUrl?: string | null }; trustAvailable: boolean };
export type Settings = { media: { mic: string; speaker: string; camera: string; noise: "off" | "standard" | "enhanced"; echo: boolean; agc: boolean }; closeAction: string; autostart: string; updateMode: string; theme: string; notifications: { enabled: boolean; sound: boolean; hideContent: boolean; dndUntil: number };
  proxy: { mode: string; rules: string; bypass: string; pac: string }; activeOrg: string | null; firstRunDone: boolean };

type Bridge = { call(ch: string, a?: unknown): Promise<unknown>; on(ev: string, cb: (d: unknown) => void): void };
export const pg = (window as unknown as { pg: Bridge }).pg;
export const call = <T = unknown>(ch: string, a?: unknown) => pg.call(ch, a) as Promise<T>;

/** Цвет плитки организации без значка — по названию, из ограниченной палитры с контрастом к белому тексту. */
const PALETTE = ["#2456d3", "#7a4fd8", "#0f8a6c", "#c2410c", "#b42357", "#0e7490", "#4d7c0f", "#9333ea"];
export function orgColor(name: string): string {
  let n = 0;
  for (const ch of name) n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[n % PALETTE.length];
}
export function initials(name: string): string {
  const w = name.trim().split(/[\s«»"'().,-]+/).filter(Boolean);
  return ((w[0]?.[0] ?? "?") + (w.length > 1 ? w[1][0] : (w[0]?.[1] ?? ""))).toUpperCase();
}

export function orgTile(o: OrgView, extra: Props = {}): HTMLElement {
  const inner = o.icon ? h("img", { src: o.icon, alt: "" }) : initials(o.name);
  return h("span", { class: "org", style: { "--c": orgColor(o.name) }, ...extra }, inner);
}
