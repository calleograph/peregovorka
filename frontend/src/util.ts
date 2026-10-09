/** Время суток с секундами в часовом поясе браузера: «10:42:13». */
export const clock = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString("ru-RU", { hour12: false }) : "");

/** Длительность словами: «4 мин 38 с», «52 с», «1 ч 05 мин». */
export function spanRu(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  const s = Math.round(seconds);
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} мин ${s % 60} с` : `${m} мин`;
  return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, "0")} мин`;
}

export const fmt = (iso: string) => new Date(iso).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });

export function duration(startIso: string, endIso: string | null): string {
  if (!endIso) return "идёт";
  const s = Math.max(0, Math.round((new Date(endIso).getTime() - new Date(startIso).getTime()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} ч ${m} мин` : m ? `${m} мин` : `${s} с`;
}

export const bytes = (n: number | null | undefined) => {
  if (n == null) return "—";
  const u = ["Б", "КБ", "МБ", "ГБ", "ТБ"]; let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
};

/** Копирование в буфер: современный API, а в незащищённом контексте/старых браузерах — запасной способ. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* пробуем запасной путь */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch { return false; }
}

/** Скачивание текста, сформированного на странице (без обращения к серверу). */
export function downloadText(content: string, name: string, mime = "text/plain;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Безопасное имя файла из названия комнаты и даты. */
export function fileBase(name: string, iso: string): string {
  const d = new Date(iso);
  const stamp = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `${name.replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 60)}_${stamp}`;
}

/** Короткий commit для показа (как `git log --oneline`). */
export const shortCommit = (c: string | null | undefined): string => (c && c !== "unknown" ? c.slice(0, 7) : "");

/** «0.1.4 · 6123022» — версия проекта и commit сборки (commit неизвестен — только версия). */
export const versionLabel = (version: string, commit?: string | null): string => {
  const c = shortCommit(commit);
  return c ? `${version} · ${c}` : version;
};
