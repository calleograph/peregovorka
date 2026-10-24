// Логика чатов без React: слияние сообщений без дублей, группировка, список переписок по событиям, разметка текста. Покрыта chatModel.test.ts.
import { linkify } from "../linkify";
import type { ChatItem, ChatMsg } from "./chatApi";

/** Добавить или обновить сообщения: повтор (событие + ответ на отправку, догрузка после переподключения) не дублирует; правка заменяет;
 *  ещё отправляемое сообщение заменяется серверным по client_id. Порядок — по id; неотправленные (без id сервера) — в конце. */
export function mergeMessages(list: ChatMsg[], incoming: ChatMsg[]): ChatMsg[] {
  if (!incoming.length) return list;
  const byId = new Map<number, ChatMsg>();
  const pending: ChatMsg[] = [];
  for (const m of list) (m.pending ? pending.push(m) : byId.set(m.id, m));
  for (const m of incoming) {
    if (m.pending) { const i = pending.findIndex((p) => p.client_id === m.client_id); if (i >= 0) pending[i] = m; else pending.push(m); continue; }
    byId.set(m.id, m);
  }
  const confirmed = new Set([...byId.values()].map((m) => m.client_id).filter(Boolean));
  const out = [...byId.values()].sort((a, b) => a.id - b.id);
  return out.concat(pending.filter((p) => !confirmed.has(p.client_id)));
}

/** Ключ дня (ГГГГ-ММ-ДД) в поясе пользователя — для разделителей дат. */
const DAY = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" });
export const dayKey = (iso: string, fmt: Intl.DateTimeFormat = DAY) => fmt.format(new Date(iso));

const LABEL = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" });
const LABEL_Y = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" });
export function dayTitle(iso: string, now: number = Date.now()): string {
  const k = dayKey(iso);
  if (k === dayKey(new Date(now).toISOString())) return "Сегодня";
  if (k === dayKey(new Date(now - 86400000).toISOString())) return "Вчера";
  return (new Date(iso).getFullYear() === new Date(now).getFullYear() ? LABEL : LABEL_Y).format(new Date(iso));
}

const HM = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
const DM = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" });
export const timeHM = (iso: string) => HM.format(new Date(iso));
/** Время в списке переписок: сегодня — часы, на неделе — день недели, раньше — дата. */
export function listTime(iso: string, now: number = Date.now()): string {
  const d = new Date(iso);
  if (dayKey(iso) === dayKey(new Date(now).toISOString())) return HM.format(d);
  if (now - d.getTime() < 6 * 86400000) return d.toLocaleDateString("ru-RU", { weekday: "short" });
  return DM.format(d).replace(".", "");
}

export interface Row { kind: "day"; key: string; title: string }
export interface MsgRow { kind: "msg"; key: string; m: ChatMsg; first: boolean; last: boolean }
/** Лента: разделители дней и серии сообщений одного автора (подряд и не дальше 5 минут) — имя и аватар только у первого в серии. */
export function rows(list: ChatMsg[], now: number = Date.now()): (Row | MsgRow)[] {
  const out: (Row | MsgRow)[] = [];
  let day = "";
  for (let i = 0; i < list.length; i++) {
    const m = list[i], prev = list[i - 1], next = list[i + 1];
    const k = dayKey(m.created_at);
    if (k !== day) { out.push({ kind: "day", key: `d${k}`, title: dayTitle(m.created_at, now) }); day = k; }
    const same = (a?: ChatMsg, b?: ChatMsg) => !!a && !!b && a.kind === "text" && b.kind === "text" && a.author_id === b.author_id && dayKey(a.created_at) === dayKey(b.created_at)
      && Math.abs(new Date(a.created_at).getTime() - new Date(b.created_at).getTime()) < 5 * 60000;
    out.push({ kind: "msg", key: m.pending ? `p${m.client_id}` : `m${m.id}`, m, first: !same(prev, m), last: !same(m, next) });
  }
  return out;
}

/** Порядок списка: закреплённые (последние закреплённые выше), затем по последнему сообщению. */
export function sortChats(items: ChatItem[]): ChatItem[] {
  return [...items].sort((a, b) => (Number(b.pinned) - Number(a.pinned)) || ((b.pinned_at ?? "").localeCompare(a.pinned_at ?? "")) || b.last_at.localeCompare(a.last_at));
}

export type ListView = { folder: "all" | "unread" | "archive" | string; text: string };
export function visibleChats(items: ChatItem[], v: ListView): ChatItem[] {
  const t = v.text.trim().toLocaleLowerCase("ru");
  return sortChats(items).filter((c) => {
    if (v.folder === "archive") { if (!c.archived) return false; } else if (c.archived) return false;
    if (v.folder === "unread" && c.unread === 0) return false;
    if (v.folder !== "all" && v.folder !== "archive" && v.folder !== "unread" && c.folder_id !== v.folder) return false;
    return !t || c.title.toLocaleLowerCase("ru").includes(t) || (c.peer?.department ?? "").toLocaleLowerCase("ru").includes(t);
  });
}

/** Новое сообщение в списке переписок: последнее сообщение, время, непрочитанные (не своё и переписка не открыта на виду). null — переписки нет в списке (надо перечитать). */
export function onNewMessage(items: ChatItem[], chatId: string, m: ChatMsg, myId: string, viewing: boolean): ChatItem[] | null {
  const i = items.findIndex((c) => c.id === chatId);
  if (i < 0) return null;
  const c = items[i];
  if (c.last && c.last.id >= m.id) return items;                     // повтор события
  const mine = m.author_id === myId;
  const next: ChatItem = { ...c, hidden: false, last: { id: m.id, author_id: m.author_id, author_name: m.author_name, kind: m.kind, text: preview(m), deleted: m.deleted, at: m.created_at },
    last_at: m.created_at, unread: mine || viewing || m.kind !== "text" ? (mine ? 0 : c.unread) : c.unread + 1, last_read_id: mine ? m.id : c.last_read_id };
  const out = items.slice();
  out[i] = next;
  return out;
}

export function preview(m: ChatMsg): string {
  if (m.deleted) return "Сообщение удалено";
  const t = m.text.replace(/\s+/g, " ").trim();
  if (t) { const p = plainText(t); return p.length > 160 ? `${p.slice(0, 159)}…` : p; }
  if (m.files.length) return m.files.every((f) => f.kind === "image") ? "Изображение" : `Файл: ${m.files[0].name}`;
  return "";
}

/** Текст без знаков разметки — для превью в списке переписок и уведомлений. */
export const plainText = (t: string) => t.replace(/```/g, "").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1").replace(/(^|[^\p{L}\p{N}_])_([^_]+)_(?![\p{L}\p{N}_])/gu, "$1$2");

export const totalUnread = (items: ChatItem[]) => items.reduce((n, c) => n + (c.muted || c.hidden ? 0 : c.unread), 0);

// ------------------------------------------------------------------------------------------------ разметка текста
export type Span =
  | { t: "text"; v: string } | { t: "link"; v: string; href: string } | { t: "b"; c: Span[] } | { t: "i"; c: Span[] } | { t: "code"; v: string } | { t: "pre"; v: string }
  | { t: "mark"; v: string } | { t: "br" };

/** Безопасная разметка сообщения (без HTML): ссылки, **жирный**, _курсив_, `код`, блок ```кода```, переносы строк. Подсветка найденного — `marks`. */
export function format(text: string, marks: string[] = []): Span[] {
  const out: Span[] = [];
  const parts = text.split(/```/);
  parts.forEach((part, i) => {
    if (i % 2 === 1 && i < parts.length - 1) { out.push({ t: "pre", v: part.replace(/^\n/, "").replace(/\n$/, "") }); return; }
    const chunk = i % 2 === 1 ? "```" + part : part;
    chunk.split("\n").forEach((line, j) => { if (j) out.push({ t: "br" }); out.push(...inline(line, marks)); });
  });
  return out;
}

function inline(line: string, marks: string[]): Span[] {
  const out: Span[] = [];
  for (const c of linkify(line)) {                                  // сначала ссылки: подчёркивания и звёздочки в адресе — не разметка
    if (c.type === "link") { out.push({ t: "link", v: c.text, href: c.href }); continue; }
    const re = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|(?<![\p{L}\p{N}_])_([^_\n]+)_(?![\p{L}\p{N}_])/gu;
    let last = 0;
    for (const m of c.text.matchAll(re)) {
      if (m.index! > last) out.push(...highlight(c.text.slice(last, m.index), marks));
      if (m[1] !== undefined) out.push({ t: "code", v: m[1] });
      else if (m[2] !== undefined) out.push({ t: "b", c: highlight(m[2], marks) });
      else out.push({ t: "i", c: highlight(m[3], marks) });
      last = m.index! + m[0].length;
    }
    if (last < c.text.length) out.push(...highlight(c.text.slice(last), marks));
  }
  return out;
}

/** Подсветка найденных слов (без учёта регистра; слово — и как начало более длинного, как ищет сервер). */
export function highlight(s: string, marks: string[]): Span[] {
  const terms = marks.map((m) => m.trim()).filter((m) => m.length > 0);
  if (!terms.length || !s) return s ? [{ t: "text", v: s }] : [];
  const esc = terms.sort((a, b) => b.length - a.length).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${esc.join("|")})`, "giu");
  const out: Span[] = [];
  let last = 0;
  for (const m of s.matchAll(re)) {
    if (m.index! > last) out.push({ t: "text", v: s.slice(last, m.index) });
    out.push({ t: "mark", v: m[0] });
    last = m.index! + m[0].length;
  }
  if (last < s.length) out.push({ t: "text", v: s.slice(last) });
  return out;
}

// ------------------------------------------------------------------------------------------------ диктовка
/** Вставка распознанного фрагмента в поле ввода у курсора: уже набранное вручную сохраняется, повтор фрагмента (тот же номер) не добавляется. */
export function insertDictated(value: string, caret: number, text: string, seq: number, seen: Set<number>): { value: string; caret: number } {
  const t = text.trim();
  if (!t || seen.has(seq)) return { value, caret };
  seen.add(seq);
  const at = Math.max(0, Math.min(caret, value.length));
  const before = value.slice(0, at), after = value.slice(at);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const tail = after && !/^\s/.test(after) ? " " : "";
  const piece = lead + (before.trim() && /[.!?]\s*$/.test(before) ? t[0].toUpperCase() + t.slice(1) : !before.trim() ? t[0].toUpperCase() + t.slice(1) : t) + tail;
  return { value: before + piece + after, caret: before.length + piece.length - tail.length };
}

export function fileSize(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} КБ`;
  return `${(n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0).replace(".", ",")} МБ`;
}
