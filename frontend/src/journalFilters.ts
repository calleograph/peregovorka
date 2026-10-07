import type { JournalFilter, JournalOp, JournalQuery } from "./api";

/** Поля, по которым можно строить условия (название на экране → поле API). */
export const FIELD_LABEL: Record<string, string> = {
  level: "Уровень", category: "Категория", event: "Событие", user: "Пользователь", room: "Комната", ip: "IP-адрес", client: "Браузер и ОС", message: "Сообщение", meeting: "Встреча (ID)",
};
export const OP_LABEL: Record<JournalOp, string> = {
  eq: "равно", ne: "не равно", contains: "содержит", not_contains: "не содержит", starts: "начинается с", gte: "не ниже",
};
export const LEVEL_LABEL: Record<string, string> = { debug: "отладка", info: "сведения", warn: "предупреждение", error: "ошибка" };
export const CATEGORY_LABEL: Record<string, string> = {
  auth: "вход", room: "комнаты", client: "клиент", device: "оборудование", network: "сеть и подключение", admin: "администрирование", llm: "протоколы и LLM",
  storage: "хранилище", asr: "распознавание", system: "система",
};
/** «Не ниже» применимо только к уровню; остальным полям — текстовые условия. */
export const opsFor = (field: string): JournalOp[] => (field === "level" ? ["gte", "eq", "ne"] : ["eq", "ne", "contains", "not_contains", "starts"]);

export interface Preset { id: string; label: string; hint: string; query: JournalQuery }

/** Быстрые наборы фильтров для типовых разборов. */
export const PRESETS: Preset[] = [
  { id: "errors", label: "Ошибки", hint: "Только уровень «ошибка»", query: { filters: [{ field: "level", op: "eq", value: "error" }] } },
  { id: "problems", label: "Проблемы", hint: "Предупреждения и ошибки", query: { filters: [{ field: "level", op: "gte", value: "warn" }] } },
  { id: "auth", label: "Входы и отказы", hint: "Категория «вход»: успешные входы, неверные пароли, блокировки", query: { filters: [{ field: "category", op: "eq", value: "auth" }] } },
  { id: "network", label: "Сеть и подключение", hint: "Обрывы, повторные подключения, ICE, медленный вход", query: { filters: [{ field: "category", op: "eq", value: "network" }] } },
  { id: "device", label: "Оборудование", hint: "Микрофон, камера, динамики, разрешения браузера", query: { filters: [{ field: "category", op: "eq", value: "device" }] } },
  { id: "slow", label: "Долгое подключение", hint: "Клиенты, у которых медиасоединение устанавливалось дольше 4 секунд", query: { filters: [{ field: "event", op: "eq", value: "ice_slow" }] } },
  { id: "llm", label: "Протоколы и LLM", hint: "Создание протоколов, ошибки обезличивания и языковой модели", query: { filters: [{ field: "category", op: "eq", value: "llm" }] } },
];

export const isEmptyQuery = (q: JournalQuery, range: string): boolean => q.filters.length === 0 && !(q.q ?? "").trim() && !range && !q.since && !q.until;

/** Добавляет условие «поле = значение» (щелчок по значению в таблице); повторное добавление того же условия не дублируется. */
export function addEquals(filters: JournalFilter[], field: string, value: string): JournalFilter[] {
  if (filters.some((f) => f.field === field && f.op === "eq" && f.value === value)) return filters;
  return [...filters, { field, op: "eq", value }];
}

/** Человекочитаемое описание условия — для подписи у применённых фильтров. */
export function describeFilter(f: JournalFilter): string {
  const v = Array.isArray(f.value) ? f.value.join(", ") : f.value;
  const shown = f.field === "level" ? (LEVEL_LABEL[v] ?? v) : f.field === "category" ? (CATEGORY_LABEL[v] ?? v) : v;
  return `${FIELD_LABEL[f.field] ?? f.field} ${OP_LABEL[f.op]} «${shown}»`;
}
