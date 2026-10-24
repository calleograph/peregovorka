import { describe, expect, it } from "vitest";
import type { ChatItem, ChatMsg } from "./chatApi";
import { format, highlight, insertDictated, mergeMessages, onNewMessage, rows, totalUnread, visibleChats } from "./chatModel";

const msg = (id: number, over: Partial<ChatMsg> = {}): ChatMsg => ({
  id, chat_id: "c1", author_id: "a", author_name: "Алиса", kind: "text", text: `m${id}`, created_at: "2026-10-24T09:00:00Z", edited_at: null, deleted: false,
  client_id: `cid${id}`, forwarded: null, reply_to: null, files: [], ...over,
});
const chat = (id: string, over: Partial<ChatItem> = {}): ChatItem => ({
  id, kind: "direct", title: id, avatar_url: null, peer: null, members: 2, role: "member", last: null, last_at: "2026-10-24T08:00:00Z", unread: 0, last_read_id: 0,
  muted: false, pinned: false, pinned_at: null, archived: false, folder_id: null, hidden: false, ...over,
});

describe("сообщения без дублей", () => {
  it("событие и ответ сервера, догрузка после переподключения — без повторов; правка заменяет; порядок по id", () => {
    let l = mergeMessages([], [msg(2), msg(1)]);
    l = mergeMessages(l, [msg(2), msg(3)]);
    l = mergeMessages(l, [msg(2, { text: "исправлено", edited_at: "x" })]);
    expect(l.map((m) => m.id)).toEqual([1, 2, 3]);
    expect(l[1].text).toBe("исправлено");
  });
  it("отправляемое сообщение показывается сразу и заменяется серверным по client_id (повтор после сбоя — одно сообщение)", () => {
    const local = msg(0, { client_id: "x1", pending: "sending" });
    let l = mergeMessages([msg(1)], [local]);
    expect(l.map((m) => m.pending ?? m.id)).toEqual([1, "sending"]);
    l = mergeMessages(l, [msg(7, { client_id: "x1" })]);
    l = mergeMessages(l, [msg(7, { client_id: "x1" })]);
    expect(l.map((m) => m.id)).toEqual([1, 7]);
  });
});

describe("лента и список", () => {
  it("разделители дней и серии одного автора", () => {
    const now = Date.parse("2026-10-24T12:00:00Z");
    const r = rows([msg(1, { created_at: "2026-10-23T09:00:00Z" }), msg(2), msg(3, { created_at: "2026-10-24T09:02:00Z" }), msg(4, { author_id: "b", created_at: "2026-10-24T09:03:00Z" })], now);
    expect(r.map((x) => (x.kind === "day" ? x.title : `${x.m.id}${x.first ? "F" : ""}${x.last ? "L" : ""}`))).toEqual(["Вчера", "1FL", "Сегодня", "2F", "3L", "4FL"]);
  });
  it("новое сообщение: непрочитанные растут только у чужого и не открытого; повтор события не считается дважды; свои — прочитаны", () => {
    const items = [chat("c1", { unread: 1, last: { id: 5, author_id: "b", author_name: "Б", kind: "text", text: "", deleted: false, at: "" } }), chat("c2")];
    const a = onNewMessage(items, "c1", msg(6, { author_id: "b" }), "me", false)!;
    expect(a[0].unread).toBe(2);
    expect(onNewMessage(a, "c1", msg(6, { author_id: "b" }), "me", false)![0].unread).toBe(2);
    expect(onNewMessage(a, "c1", msg(7, { author_id: "b" }), "me", true)![0].unread).toBe(2);
    expect(onNewMessage(a, "c1", msg(8, { author_id: "me" }), "me", false)![0].unread).toBe(0);
    expect(onNewMessage(a, "zz", msg(9), "me", false)).toBeNull();
  });
  it("папки, архив, непрочитанные, поиск; закреплённые выше; заглушённые не входят в общий счётчик", () => {
    const items = [chat("Работа", { folder_id: "f1", unread: 2 }), chat("Архивный", { archived: true }), chat("Важный", { pinned: true, pinned_at: "2026-10-01T00:00:00Z" }),
      chat("Тихий", { muted: true, unread: 5 })];
    expect(visibleChats(items, { folder: "all", text: "" }).map((c) => c.id)).toEqual(["Важный", "Работа", "Тихий"]);
    expect(visibleChats(items, { folder: "f1", text: "" }).map((c) => c.id)).toEqual(["Работа"]);
    expect(visibleChats(items, { folder: "archive", text: "" }).map((c) => c.id)).toEqual(["Архивный"]);
    expect(visibleChats(items, { folder: "unread", text: "" }).map((c) => c.id)).toEqual(["Работа", "Тихий"]);
    expect(visibleChats(items, { folder: "all", text: "раб" }).map((c) => c.id)).toEqual(["Работа"]);
    expect(totalUnread(items)).toBe(2);
  });
});

describe("разметка текста — без HTML", () => {
  it("ссылки, жирный, курсив, код, блок кода, переносы; теги остаются текстом", () => {
    const f = format("Смотри **важно** и _тут_ `npm ci`\nhttps://example.org/a_b_c <b>x</b>\n```\nline1\nline2\n```");
    expect(f).toContainEqual({ t: "b", c: [{ t: "text", v: "важно" }] });
    expect(f).toContainEqual({ t: "i", c: [{ t: "text", v: "тут" }] });
    expect(f).toContainEqual({ t: "code", v: "npm ci" });
    expect(f).toContainEqual({ t: "link", v: "https://example.org/a_b_c", href: "https://example.org/a_b_c" });
    expect(f).toContainEqual({ t: "text", v: " <b>x</b>" });
    expect(f).toContainEqual({ t: "pre", v: "line1\nline2" });
    expect(format("javascript:alert(1)").some((s) => s.t === "link")).toBe(false);
    expect(format("snake_case_name").some((s) => s.t === "i")).toBe(false);
  });
  it("подсветка найденного без учёта регистра", () => {
    expect(highlight("Отчёт и ОТЧЁТЫ", ["отчёт"])).toEqual([{ t: "mark", v: "Отчёт" }, { t: "text", v: " и " }, { t: "mark", v: "ОТЧЁТ" }, { t: "text", v: "Ы" }]);
  });
});

describe("диктовка в поле ввода", () => {
  it("фрагменты встают у курсора, ручной текст сохраняется, повтор фрагмента не добавляется, начало предложения — с заглавной", () => {
    const seen = new Set<number>();
    let s = insertDictated("", 0, "добрый день", 1, seen);
    expect(s.value).toBe("Добрый день");
    s = insertDictated(s.value + ".", s.value.length + 1, "отчёт готов", 2, seen);
    expect(s.value).toBe("Добрый день. Отчёт готов");
    expect(insertDictated(s.value, s.caret, "отчёт готов", 2, seen).value).toBe(s.value);
    const mid = insertDictated("Привет коллеги", 6, "дорогие", 3, seen);
    expect(mid.value).toBe("Привет дорогие коллеги");
  });
});

describe("превью без разметки", () => {
  it("знаки разметки убираются, текст остаётся", async () => {
    const { plainText } = await import("./chatModel");
    expect(plainText("Смотри **важное**, `код` и _курсив_ snake_case")).toBe("Смотри важное, код и курсив snake_case");
  });
});
