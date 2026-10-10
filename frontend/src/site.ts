import { useSyncExternalStore } from "react";

/** Оформление и сведения конкретной установки (названия, цвета, организация, контакты, документы). Единый источник для всего интерфейса:
 *  загружается один раз при открытии страницы с `/api/v1/public/site` и применяется без пересборки; на чистой установке — стандартное оформление Peregovorka. */
export interface SiteDoc { kind: string; title: string; version: number; require_consent: boolean; published_at?: string | null }
export interface Site {
  name: string; short_name: string; subtitle: string; description: string; theme: "system" | "light" | "dark";
  primary_color: string; accent2_color: string;
  assets: { logo: string | null; logo_compact: string | null; favicon: string | null };
  org: { full: string; short: string; url: string; unit: string; legal_name: string; address: string };
  footer_text: string;
  support: { support_email: string; support_phone: string; support_url: string; portal_url: string; support_text: string } | null;
  welcome_text: string; guest_text: string; recording_text: string;
  documents: SiteDoc[]; customized: boolean;
}

export const DEFAULT_SITE: Site = {
  name: "Peregovorka", short_name: "Peregovorka", subtitle: "Видеовстречи с автоматической стенограммой и протоколом", description: "", theme: "system",
  primary_color: "", accent2_color: "", assets: { logo: null, logo_compact: null, favicon: null },
  org: { full: "", short: "", url: "", unit: "", legal_name: "", address: "" }, footer_text: "", support: null,
  welcome_text: "", guest_text: "", recording_text: "", documents: [], customized: false,
};

let current: Site = DEFAULT_SITE;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

export const getSite = (): Site => current;
export function useSite(): Site { return useSyncExternalStore((cb) => { subs.add(cb); return () => { subs.delete(cb); }; }, getSite, getSite); }

/** Установить значения (из ответа сервера или из предпросмотра в админке) и применить к странице. */
export function setSite(s: Site, doc: Document = document): void {
  current = s;
  applySite(s, doc);
  emit();
}

if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => { applySite(current); });      // «как в системе»: следим за переключением темы устройства
}
/** Тема пользователя применяется сразу при загрузке модуля — до ответа сервера, чтобы страница не мигала другой темой. */
function applyUserThemeEarly(): void {
  if (typeof document === "undefined") return;
  const t = getUserTheme();
  if (t !== "system") document.documentElement.setAttribute("data-theme", t);
}

export async function loadSite(): Promise<Site> {
  try {
    const r = await fetch("/api/v1/public/site", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.ok) { const s = { ...DEFAULT_SITE, ...(await r.json()) } as Site; setSite(s); return s; }
  } catch { /* сервер недоступен: остаётся стандартное оформление */ }
  return current;
}

// ------------------------------------------------------------------------------------------ цвета
const hex = (c: string): [number, number, number] | null => (/^#[0-9a-f]{6}$/i.test(c) ? [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)) as [number, number, number] : null);
const toHex = (rgb: number[]) => "#" + rgb.map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0")).join("");
export const mix = (a: string, b: string, t: number): string => { const x = hex(a), y = hex(b); return x && y ? toHex(x.map((v, i) => v * (1 - t) + y[i] * t)) : a; };
export function luminance(c: string): number {
  const x = hex(c);
  if (!x) return 0;
  const [r, g, b] = x.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
/** Читаемый цвет текста на фоне заданного цвета (белый или почти чёрный). */
export const onColor = (bg: string): string => {
  const l = luminance(bg);
  const withWhite = 1.05 / (l + 0.05), withDark = (l + 0.05) / (luminance("#0a1226") + 0.05);      // берём цвет текста с большим контрастом
  return withDark > withWhite ? "#0a1226" : "#ffffff";
};

export interface Palette { accent: string; hover: string; soft: string; text: string; link: string; grad: string; gradHover: string }
/** Набор значений акцента из фирменного цвета; в тёмной теме цвет осветляется, чтобы оставаться читаемым на тёмном фоне. */
export function palette(primary: string, second: string, dark: boolean): Palette | null {
  if (!hex(primary)) return null;
  const base = dark ? mix(primary, "#ffffff", luminance(primary) < 0.2 ? 0.38 : 0.12) : primary;
  const accent = base;
  const hover = dark ? mix(accent, "#ffffff", 0.18) : mix(accent, "#000000", 0.16);
  const soft = dark ? mix("#161b24", accent, 0.22) : mix("#ffffff", accent, 0.12);
  const b = hex(second) ? (dark ? mix(second, "#ffffff", 0.12) : second) : mix(accent, "#7a5cf0", 0.35);
  return { accent, hover, soft, text: onColor(accent), link: dark ? mix(accent, "#ffffff", 0.1) : accent,
           grad: `linear-gradient(135deg, ${accent} 0%, ${b} 100%)`, gradHover: `linear-gradient(135deg, ${hover} 0%, ${mix(b, "#000000", dark ? 0 : 0.12)} 100%)` };
}

const VARS = ["--accent", "--accent-hover", "--accent-soft", "--accent-text", "--link", "--grad", "--grad-hover", "--brand-b"];

// ------------------------------------------------------------------------------------------ применение
export type ThemeId = "system" | "light" | "dark" | "diamond" | "amber" | "corporate";
/** Темы личного кабинета. «Как в системе» — по умолчанию; три пастельные темы имеют собственный акцент (фирменный цвет организации к ним не применяется). */
export const THEMES: { id: ThemeId; label: string; hint: string; swatch: [string, string, string] }[] = [
  { id: "system", label: "Как в системе", hint: "Светлая или тёмная — по настройке устройства", swatch: ["#f3f5f8", "#161b24", "#2456d3"] },
  { id: "light", label: "Светлая", hint: "Нейтральная светлая", swatch: ["#f3f5f8", "#ffffff", "#2456d3"] },
  { id: "dark", label: "Тёмная", hint: "Нейтральная тёмная", swatch: ["#0e1218", "#161b24", "#6f98ff"] },
  { id: "diamond", label: "Бриллиант", hint: "Серебристо-лавандовая, мягкая", swatch: ["#eceff7", "#fafbfe", "#5463c4"] },
  { id: "amber", label: "Янтарная", hint: "Тёплая пастельно-оранжевая", swatch: ["#f7f0e6", "#fffaf2", "#b5601d"] },
  { id: "corporate", label: "Корпоративная", hint: "Спокойная голубая", swatch: ["#e8f0f7", "#f7fafd", "#2f6fa8"] },
];
const THEME_KEY = "pg:theme";
const NEUTRAL = new Set<ThemeId>(["system", "light", "dark"]);

export function getUserTheme(): ThemeId {
  try { const v = localStorage.getItem(THEME_KEY) as ThemeId | null; if (v && THEMES.some((t) => t.id === v)) return v; } catch { /* хранилище недоступно */ }
  return "system";
}
/** Тема этого пользователя (в браузере, как и звуки); «как в системе» — по умолчанию. Применяется сразу, без перезагрузки. */
export function setUserTheme(t: ThemeId): void {
  try { if (t === "system") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, t); } catch { /* ignore */ }
  applySite(current);
  emit();
}
/** Итоговая тема: выбор пользователя важнее темы, заданной администратором для установки; та, в свою очередь, важнее системной. */
export function effectiveTheme(site: Site, user: ThemeId = getUserTheme()): ThemeId { return user !== "system" ? user : site.theme; }

export function isDark(site: Site, win: Window = window, user: ThemeId = getUserTheme()): boolean {
  const t = effectiveTheme(site, user);
  if (t !== "system") return t === "dark";
  return typeof win.matchMedia === "function" && win.matchMedia("(prefers-color-scheme: dark)").matches;
}

function link(doc: Document, rel: string): HTMLLinkElement {
  let l = doc.querySelector<HTMLLinkElement>(`link[rel="${rel}"][data-site]`);
  if (!l) { l = doc.createElement("link"); l.rel = rel; l.dataset.site = "1"; doc.head.appendChild(l); }
  return l;
}

export function applySite(s: Site, doc: Document = document): void {
  const root = doc.documentElement;
  const eff = effectiveTheme(s);
  if (eff === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", eff);
  const p = NEUTRAL.has(eff) ? palette(s.primary_color, s.accent2_color, isDark(s)) : null;      // у цветных тем свой акцент
  if (p) {
    const set: Record<string, string> = { "--accent": p.accent, "--accent-hover": p.hover, "--accent-soft": p.soft, "--accent-text": p.text, "--link": p.link, "--grad": p.grad, "--grad-hover": p.gradHover };
    for (const [k, v] of Object.entries(set)) root.style.setProperty(k, v);
  } else for (const k of VARS) root.style.removeProperty(k);
  // значок вкладки: загруженный организацией (адрес содержит версию, поэтому после смены браузер не покажет старый); иначе стандартные значки из index.html
  const std = Array.from(doc.querySelectorAll<HTMLLinkElement>('link[rel="icon"]:not([data-site])'));
  const own = doc.querySelector<HTMLLinkElement>('link[rel="icon"][data-site]');
  if (s.assets.favicon) {
    const l = link(doc, "icon");
    l.type = "image/png"; l.href = s.assets.favicon;
    std.forEach((x) => { x.dataset.siteOff = x.getAttribute("href") ?? ""; x.removeAttribute("href"); });
  } else {
    own?.remove();
    std.forEach((x) => { if (x.dataset.siteOff && !x.getAttribute("href")) x.setAttribute("href", x.dataset.siteOff); });
  }
  const man = doc.querySelector<HTMLLinkElement>('link[rel="manifest"]');
  if (man) { if (!man.dataset.std) man.dataset.std = man.getAttribute("href") ?? ""; man.setAttribute("href", s.customized ? "/api/v1/public/site/manifest" : man.dataset.std); }
  const theme = doc.querySelector<HTMLMetaElement>('meta[name="theme-color"]:not([media])');
  if (theme && s.primary_color) theme.content = s.primary_color;
  const desc = doc.querySelector<HTMLMetaElement>('meta[name="description"]');
  if (desc) desc.content = s.description || s.subtitle;
}

/** Название для вкладки и подписей. */
export const brandName = (s: Site) => s.name || "Peregovorka";

applyUserThemeEarly();       // в конце модуля: к этому моменту объявлены все константы
