/** Свёрнутые разделы левого меню администрирования: хранится список названий свёрнутых разделов (по умолчанию все развёрнуты). */
const KEY = "pg:adminNav";

export function loadCollapsed(storage: Pick<Storage, "getItem"> | null = safeStorage()): Set<string> {
  try {
    const raw = storage?.getItem(KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : []);
  } catch { return new Set(); }
}

export function saveCollapsed(set: Set<string>, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try { storage?.setItem(KEY, JSON.stringify([...set])); } catch { /* хранилище недоступно: состояние не запомнится, меню работает */ }
}

export const toggleGroup = (set: Set<string>, title: string): Set<string> => { const n = new Set(set); if (n.has(title)) n.delete(title); else n.add(title); return n; };
export const expandAll = (): Set<string> => new Set();
export const collapseAll = (titles: string[]): Set<string> => new Set(titles);

function safeStorage(): Storage | null { try { return window.localStorage; } catch { return null; } }
