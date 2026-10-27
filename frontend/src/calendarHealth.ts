import type { CalendarEvents } from "./calendarApi";

/** text — короткое понятное сообщение; detail — настоящий текст ошибки Exchange (не скрывается, показывается по кнопке «Подробности»). */
export type Health = { kind: "ok" | "sync" | "stale" | "down" | "denied" | "relogin"; text: string; detail?: string };

/** Состояние календаря словами: подключён, идёт синхронизация, нет связи с Exchange (показанные данные могут устареть), недостаточно прав, нужна повторная авторизация. */
export function healthOf(o: { loading: boolean; err: string; code: string; data: CalendarEvents | null; lastOk: Date | null }): Health {
  const at = o.lastOk ? ` Данные на ${o.lastOk.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}.` : "";
  if (o.code === "not_connected") return { kind: "relogin", text: "Требуется повторное подключение календаря: согласие больше не действует." };
  if (o.code === "unauthorized") return { kind: "relogin", text: "Требуется повторный вход в Peregovorka." };
  if (o.code === "forbidden" || o.code === "group" || /прав|отказал в доступе/i.test(o.err)) return { kind: "denied", text: "Недостаточно прав для чтения календаря.", detail: o.err };
  if (o.data?.stale) return { kind: "stale", text: `Нет соединения с Exchange: показаны сохранённые события, они могли устареть.${at}`, detail: o.data.error || undefined };
  if (o.err || o.data?.error) return { kind: "down", text: `Не удалось обновить календарь из Exchange.${at}`, detail: o.err || o.data?.error || undefined };
  if (o.loading) return { kind: "sync", text: "Выполняется синхронизация с Exchange…" };
  return { kind: "ok", text: o.lastOk ? `Календарь подключён. Обновлено в ${o.lastOk.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}.` : "Календарь подключён." };
}
