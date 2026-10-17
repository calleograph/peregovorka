import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Скрипт надстройки — обычный (не модульный) файл для Outlook: исполняем его исходник как есть, с «module» для экспорта.
const SRC = readFileSync(new URL("../public/outlook/commands.js", import.meta.url), "utf-8");
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const load = (): any => { const mod = { exports: {} as Record<string, unknown> }; new Function("module", "exports", SRC)(mod, mod.exports); return mod.exports; };
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const pg: any = load();

describe("надстройка Outlook: чистые функции", () => {
  it("вставляет блок конференции и заменяет его при повторном нажатии (без дублей)", () => {
    const body = "<p>Повестка</p>";
    const once = pg.replaceBlock(body, "<p>Ссылка 1</p>");
    expect(pg.hasBlock(once)).toBe(true);
    expect(once.startsWith(body)).toBe(true);
    const twice = pg.replaceBlock(once + "<p>Подпись</p>", "<p>Ссылка 2</p>");
    expect(twice.split(pg.START).length - 1).toBe(1);
    expect(twice).toContain("Ссылка 2");
    expect(twice).not.toContain("Ссылка 1");
    expect(twice).toContain("Подпись");
  });

  it("если пользователь удалил ссылку из текста, блок возвращается", () => {
    const withBlock = pg.replaceBlock("<p>Текст</p>", "<p>Ссылка</p>");
    const removed = withBlock.replace(/<!--pg-conf-->[\s\S]*<!--\/pg-conf-->/, "");
    expect(pg.hasBlock(removed)).toBe(false);
    expect(pg.hasBlock(pg.replaceBlock(removed, "<p>Ссылка</p>"))).toBe(true);
  });

  it("спецсимволы в ссылке не ломают подстановку", () => {
    const out = pg.replaceBlock(pg.replaceBlock("", "<a>$&</a>"), "<a>$1 $$ \\ </a>");
    expect(out).toContain("$1 $$ \\ ");
    expect(out.split(pg.START).length - 1).toBe(1);
  });

  it("ключ события — достаточно длинная случайная строка допустимого вида", () => {
    const keys = new Set(Array.from({ length: 50 }, () => pg.newKey()));
    expect(keys.size).toBe(50);
    for (const k of keys) expect(/^[A-Za-z0-9_-]{16,80}$/.test(k as string)).toBe(true);
  });

  it("время уходит в UTC (ISO), независимо от часового пояса клиента", () => {
    expect(pg.toIso(new Date("2026-10-20T10:00:00+03:00"))).toBe("2026-10-20T07:00:00.000Z");
  });

  it("токен: берётся из хранилища, пока не истёк (с запасом минута), иначе нужен новый вход", () => {
    const mem: Record<string, string> = {};
    const st = { getItem: (k: string) => mem[k] ?? null, setItem: (k: string, v: string) => { mem[k] = v; } };
    expect(pg.readToken(st, 1000)).toBeNull();
    pg.saveToken(st, "pgo_abc", 3600, 1000);
    expect(pg.readToken(st, 2000)).toBe("pgo_abc");
    expect(pg.readToken(st, 1000 + 3600 * 1000 - 30_000)).toBeNull();           // меньше минуты до конца — обновляем заранее
    expect(pg.readToken(null, 1000)).toBeNull();
    expect(pg.readToken({ getItem: () => "{не json" }, 1000)).toBeNull();
  });

  it("текст ошибки сервера берётся из detail.message", () => {
    expect(pg.errorText({ status: 403, body: { detail: { message: "Нельзя" } } })).toBe("Нельзя");
    expect(pg.errorText({ status: 422, body: { detail: "Плохо" } })).toBe("Плохо");
    expect(pg.errorText({ status: 500, body: {} })).toBe("Ошибка сервера (500)");
  });
});

describe("команда insertPeregovorkaMeeting на фиктивном Office.js", () => {
  type Handler = (e: { completed: (o: { allowEvent: boolean }) => void }) => void;
  let handler: Handler;
  let body: string;
  let location: string;
  let props: Record<string, string>;
  let fetchMock: ReturnType<typeof vi.fn>;
  let notified: string[];
  let store: Record<string, string>;

  const ok = (fn: (cb: (r: unknown) => void) => void) => fn;
  beforeEach(async () => {
    vi.resetModules();
    body = "<p>Повестка</p>"; location = ""; props = {}; notified = []; store = {};
    const mk = (value: unknown) => ({ getAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value }) });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).Office = {
      AsyncResultStatus: { Succeeded: "succeeded" }, EventType: { DialogEventReceived: "e", DialogMessageReceived: "m" },
      onReady: (cb: () => void) => cb(),
      actions: { associate: (_n: string, f: Handler) => { handler = f; } },
      context: {
        ui: { displayDialogAsync: ok(() => { throw new Error("диалог не должен открываться при валидном токене"); }) },
        mailbox: { item: {
          loadCustomPropertiesAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value: { get: (k: string) => props[k], set: (k: string, v: string) => { props[k] = v; }, saveAsync: (cb2: (r: unknown) => void) => cb2({ status: "succeeded" }) } }),
          subject: mk("Планёрка"), start: mk(new Date("2026-10-20T10:00:00Z")), end: mk(new Date("2026-10-20T10:30:00Z")), recurrence: mk(null),
          body: { getAsync: (_t: string, cb: (r: unknown) => void) => cb({ status: "succeeded", value: body }), setAsync: (v: string, _o: unknown, cb: (r: unknown) => void) => { body = v; cb({ status: "succeeded" }); } },
          location: { setAsync: (v: string, cb: (r: unknown) => void) => { location = v; cb({ status: "succeeded" }); } },
          notificationMessages: { replaceAsync: (_id: string, m: { message: string }) => { notified.push(m.message); } },
        } },
      },
    };
    store["pg_outlook_token"] = JSON.stringify({ token: "pgo_x", exp: Date.now() + 3600_000 });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; } };
    fetchMock = vi.fn(async () => ({ status: 200, json: async () => ({ url: "https://meet.example.org/m/abc", invite_html: "<p>Ссылка https://meet.example.org/m/abc</p>" }) }));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).fetch = fetchMock;
    load();
  });

  const press = () => new Promise<boolean>((resolve) => handler({ completed: (o) => resolve(o.allowEvent) }));

  it("создаёт конференцию, вставляет блок и ссылку; повторное нажатие использует тот же ключ и не дублирует блок", async () => {
    expect(await press()).toBe(true);
    const sent1 = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(sent1).toMatchObject({ subject: "Планёрка", start: "2026-10-20T10:00:00.000Z", end: "2026-10-20T10:30:00.000Z", recurring: false });
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer pgo_x");
    expect(body.split("<!--pg-conf-->").length - 1).toBe(1);
    expect(location).toBe("https://meet.example.org/m/abc");
    expect(await press()).toBe(true);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).key).toBe(sent1.key);              // тот же ключ → сервер вернёт ту же комнату
    expect(body.split("<!--pg-conf-->").length - 1).toBe(1);
  });

  it("при ошибке сервера письмо не блокируется ссылкой-пустышкой: показано уведомление, событие отклонено", async () => {
    fetchMock.mockResolvedValueOnce({ status: 403, json: async () => ({ detail: { message: "Создавать конференции вам не разрешено." } }) });
    expect(await press()).toBe(false);
    expect(notified[0]).toContain("не разрешено");
    expect(body).toBe("<p>Повестка</p>");
  });
});
