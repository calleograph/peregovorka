import { useEffect } from "react";

/**
 * Защита от потери несохранённых изменений: формы сообщают, что в них есть правки; при закрытии вкладки браузер спросит подтверждение,
 * а переход между разделами администрирования (`confirmLeave`) — спросит сам. Состояние живёт только в памяти страницы.
 */
const dirty = new Set<string>();
let bound = false;

const warn = (e: BeforeUnloadEvent) => { if (dirty.size) { e.preventDefault(); e.returnValue = ""; } };

export function setDirty(key: string, value: boolean): void {
  if (value) dirty.add(key); else dirty.delete(key);
  if (typeof window === "undefined") return;
  if (dirty.size && !bound) { window.addEventListener("beforeunload", warn); bound = true; }
  if (!dirty.size && bound) { window.removeEventListener("beforeunload", warn); bound = false; }
}

export const hasDirty = (): boolean => dirty.size > 0;

/** Можно ли уйти с экрана: без правок — да; иначе спрашивает пользователя. `ask` подменяется в тестах. */
export function confirmLeave(ask: (text: string) => boolean = (t) => window.confirm(t)): boolean {
  if (!dirty.size) return true;
  const ok = ask("В форме есть несохранённые изменения. Уйти без сохранения?");
  if (ok) dirty.clear();
  if (ok && bound && typeof window !== "undefined") { window.removeEventListener("beforeunload", warn); bound = false; }
  return ok;
}

/** Хук формы: `useDirty("настройки outlook", isDirty)`. При размонтировании отметка снимается. */
export function useDirty(key: string, value: boolean): void {
  useEffect(() => { setDirty(key, value); return () => setDirty(key, false); }, [key, value]);
}
