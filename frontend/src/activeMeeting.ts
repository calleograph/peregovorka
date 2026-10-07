import { useSyncExternalStore } from "react";

// Идёт ли в ЭТОЙ вкладке встреча. Правило интерфейса: пока встреча идёт, вкладка с комнатой никогда не используется для перехода в другие
// разделы (администрирование, история, протоколы, ссылки из чата) — всё это открывается в новой вкладке, иначе уход со страницы оборвёт
// соединение с комнатой. Состояние выставляет страница комнаты; верхняя панель приложения читает его.
let active: string | null = null;
const subs = new Set<() => void>();

export function setActiveMeeting(id: string | null): void {
  if (active === id) return;
  active = id;
  subs.forEach((f) => f());
}
export const getActiveMeeting = (): string | null => active;
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
export const useActiveMeeting = (): string | null => useSyncExternalStore(subscribe, getActiveMeeting, () => null);
