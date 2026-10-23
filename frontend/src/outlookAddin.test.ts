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

describe("надстройка Outlook: проверка времени и тексты ошибок", () => {
  const H = 3600_000;
  const now = Date.parse("2026-10-20T12:00:00Z");
  it("время проверяется по правилам сервера: начавшаяся встреча — не прошедшая", () => {
    expect(pg.checkTimes(new Date(now + H), new Date(now + 2 * H), false, now)).toBeNull();
    expect(pg.checkTimes(new Date(now - H), new Date(now + H), false, now)).toBeNull();                  // идёт сейчас
    expect(pg.checkTimes(new Date(now - 2 * H), new Date(now - 30 * 60_000), false, now)).toBeNull();     // закончилась меньше часа назад — сервер ещё примет
    expect(pg.checkTimes(new Date(now - 3 * H), new Date(now - 2 * H), false, now)).toBe("in_past");
    expect(pg.checkTimes(new Date(now + 2 * H), new Date(now + H), false, now)).toBe("bad_time");
    expect(pg.checkTimes(new Date(now + H), new Date(now + H), false, now)).toBe("bad_time");
    expect(pg.checkTimes(new Date(now + H), new Date(now + 30 * H), false, now)).toBe("too_long");
    expect(pg.checkTimes(new Date(now + H), new Date(now + 30 * H), true, now)).toBeNull();              // серия
    expect(pg.checkTimes(new Date(now + 800 * 24 * H), new Date(now + 800 * 24 * H + H), false, now)).toBe("too_far");
    expect(pg.checkTimes(null, new Date(now), false, now)).toBe("no_time");
    expect(pg.checkTimes(new Date("не дата"), new Date(now), false, now)).toBe("no_time");
  });

  it("ответ сервера превращается в код; пользователь видит объяснение, а не JSON и HTTP-код", () => {
    const e422 = pg.fromResponse({ status: 422, body: { detail: { code: "in_past", message: "Встреча уже закончилась." } } }, "api");
    expect(e422.code).toBe("in_past");
    expect(pg.explain(e422).text).toBe("Встреча уже завершилась. Укажите актуальные дату и время, затем попробуйте снова.");
    const e500 = pg.explain(pg.fromResponse({ status: 500, body: { detail: "Traceback (most recent call last)" } }, "api"));
    expect(e500.code).toBe("server");
    expect(e500.text).not.toMatch(/500|Traceback|\{/);
    expect(e500.detail).toContain("HTTP 500");                                             // сведения — только в подробностях
    expect(pg.fromResponse({ status: 401, body: {} }, "api").code).toBe("auth_expired");
    expect(pg.fromResponse({ status: 429, body: {} }, "api").code).toBe("rate");
    expect(pg.explain(pg.fromResponse({ status: 403, body: { detail: "Создавать конференции из Outlook вам не разрешено." } }, "api")).text).toContain("не разрешено");
    expect(pg.explain(pg.PgError("auth_cancelled", "auth")).kind).toBe("info");          // закрытое окно входа — не критическая ошибка
    expect(pg.explain(pg.PgError("invite_failed", "invite")).kind).toBe("partial");
  });

  it("ожидание ограничено: тайм-аут даёт ошибку с кодом и этапом", async () => {
    await expect(pg.timeout(new Promise(() => {}), 20, "timeout", "api")).rejects.toMatchObject({ code: "timeout", stage: "api" });
    await expect(pg.timeout(Promise.resolve(5), 20, "timeout", "api")).resolves.toBe(5);
  });
});

describe("ядро и скрытые команды на фиктивном Office.js", () => {
  type Cmd = (e: { completed: (o?: { allowEvent: boolean }) => void }) => void;
  let handlers: Record<string, Cmd>;
  let body: string;
  let location: string;
  let props: Record<string, string>;
  let saved: Record<string, string>;
  let fetchMock: ReturnType<typeof vi.fn>;
  let notified: { type: string; message: string }[];
  let store: Record<string, string>;
  let onReadyCalls: number;
  let dialog: { url: string; reply: (m: unknown) => void; closeByUser: () => void } | null;
  let bodyFails: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let mod: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const G = globalThis as any;
  const ROOM = { url: "https://meet.example.org/m/abc", invite_html: "<p>Ссылка https://meet.example.org/m/abc</p>", created: true };

  beforeEach(() => {
    handlers = {}; dialog = null; bodyFails = false;
    body = "<p>Повестка</p>"; location = ""; props = {}; saved = {}; notified = []; store = {}; onReadyCalls = 0;
    const mk = (value: unknown) => ({ getAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value }) });
    G.Office = {
      AsyncResultStatus: { Succeeded: "succeeded", Failed: "failed" }, MailboxEnums: { RestVersion: { v2_0: "v2.0" } }, EventType: { DialogEventReceived: "e", DialogMessageReceived: "m" },
      onReady: (cb: () => void) => { onReadyCalls++; cb(); },
      actions: { associate: (n: string, f: Cmd) => { handlers[n] = f; } },
      context: {
        ui: { displayDialogAsync: (url: string, _o: unknown, cb: (r: unknown) => void) => {
          const ev: Record<string, (a: unknown) => void> = {};
          dialog = { url, reply: (m) => ev.m({ message: JSON.stringify(m) }), closeByUser: () => ev.e({ error: 12006 }) };
          cb({ status: "succeeded", value: { addEventHandler: (t: string, f: (a: unknown) => void) => { ev[t] = f; }, close: () => {} } });
        } },
        mailbox: { userProfile: { emailAddress: "owner@example.org" }, convertToRestId: (id: string) => `rest-${id}`, item: {
          getItemIdAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value: "EWS-ITEM-ID-123" }),
          loadCustomPropertiesAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value: { get: (k: string) => props[k], set: (k: string, v: string) => { props[k] = v; },
            saveAsync: (cb2: (r: unknown) => void) => { saved = { ...props }; cb2({ status: "succeeded" }); } } }),
          subject: mk("Планёрка"), start: mk(new Date(Date.now() + 3600_000)), end: mk(new Date(Date.now() + 5400_000)), recurrence: mk(null),
          body: { getAsync: (_t: string, cb: (r: unknown) => void) => cb({ status: "succeeded", value: body }),
            setAsync: (v: string, _o: unknown, cb: (r: unknown) => void) => { if (bodyFails) { cb({ status: "failed", error: { code: 9041, message: "нельзя" } }); return; } body = v; cb({ status: "succeeded" }); } },
          location: { setAsync: (v: string, cb: (r: unknown) => void) => { location = v; cb({ status: "succeeded" }); } },
          notificationMessages: { replaceAsync: (_id: string, m: { type: string; message: string }) => { notified.push(m); } },
        } },
      },
    };
    store["pg_outlook_token"] = JSON.stringify({ token: "pgo_x", exp: Date.now() + 3600_000 });
    G.localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; }, removeItem: (k: string) => { delete store[k]; } };
    G.location = { origin: "https://meet.example.org" };
    fetchMock = vi.fn(async (url: string) => (url.endsWith("/log") ? { status: 204, json: async () => ({}) } : { status: 200, json: async () => ROOM }));
    G.fetch = fetchMock;
    delete G.insertPeregovorkaMeeting;
    mod = load();
  });

  const calls = (path: string) => fetchMock.mock.calls.filter((c) => c[0] === "/api/v1/outlook" + path);
  const press = (fn?: Cmd) => new Promise<unknown>((resolve) => (fn || handlers.insertPeregovorkaMeeting)({ completed: (o) => resolve(o) }));
  const send = () => new Promise<boolean>((resolve) => handlers.onAppointmentSendHandler({ completed: (o) => resolve(!!o && o.allowEvent) }));

  it("страница функций сообщает Office о готовности, а команда доступна и по глобальному имени (Outlook с Mailbox ≤ 1.9 ищет её в window)", () => {
    expect(onReadyCalls).toBeGreaterThan(0);
    expect(typeof G.insertPeregovorkaMeeting).toBe("function");
    expect(typeof G.onAppointmentSendHandler).toBe("function");
  });

  it("команда старого манифеста: создаёт конференцию, вставляет блок и ссылку, завершается без allowEvent; повтор — тот же ключ и без дубля", async () => {
    expect(await press(G.insertPeregovorkaMeeting)).toBeUndefined();                    // обычная команда: completed() без параметров OnSend
    const sent1 = JSON.parse(calls("/meetings")[0][1].body);
    expect(sent1).toMatchObject({ subject: "Планёрка", recurring: false, organizer: "owner@example.org", ews_id: "EWS-ITEM-ID-123", rest_id: "rest-EWS-ITEM-ID-123" });
    expect(calls("/meetings")[0][1].headers.Authorization).toBe("Bearer pgo_x");
    expect(body.split("<!--pg-conf-->").length - 1).toBe(1);
    expect(location).toBe(ROOM.url);
    expect(notified[0].type).toBe("informationalMessage");
    await press();
    expect(JSON.parse(calls("/meetings")[1][1].body).key).toBe(sent1.key);
    expect(body.split("<!--pg-conf-->").length - 1).toBe(1);
  });

  it("ошибка: команда завершается сразу, даже если сервер журнала не отвечает вовсе", async () => {
    fetchMock.mockImplementation((url: string) => (url.endsWith("/log") ? new Promise(() => {}) : Promise.resolve({ status: 403, json: async () => ({ detail: { message: "Создавать конференции вам не разрешено." } }) })));
    await press();
    expect(notified[0].message).toContain("не разрешено");
    expect(body).toBe("<p>Повестка</p>");
    expect(calls("/log").length).toBe(1);                                                 // отчёт отправлен, но его не ждали
  });

  it("Outlook не отвечает на запрос свойств — команда всё равно завершается по пределу времени", async () => {
    vi.useFakeTimers();
    try {
      G.Office.context.mailbox.item.loadCustomPropertiesAsync = () => { /* никогда не отвечает */ };
      let done = false;
      handlers.insertPeregovorkaMeeting({ completed: () => { done = true; } });
      await vi.advanceTimersByTimeAsync(mod.T.office + 100);
      expect(done).toBe(true);
      expect(notified[0].message).toContain("Outlook не ответил");
    } finally { vi.useRealTimers(); }
  });

  it("ключ события сохраняется до запроса: сбой сети после создания комнаты не даёт дубля при повторе", async () => {
    fetchMock.mockImplementationOnce(async () => { expect(saved.pg_key).toBeTruthy(); throw new TypeError("Failed to fetch"); });
    const item = G.Office.context.mailbox.item;
    await expect(mod.prepare(item).then((p: { payload: unknown }) => mod.requestRoom(p.payload, "pgo_x"))).rejects.toMatchObject({ code: "network", stage: "api" });
    const p2 = await mod.prepare(item);
    expect(p2.isNew).toBe(false);
    expect(p2.key).toBe(saved.pg_key);
  });

  it("сервер не ответил вовремя — ошибка timeout, а не бесконечное ожидание", async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockImplementation(() => new Promise(() => {}));
      const r = mod.requestRoom({ key: "k".repeat(20) }, "pgo_x").catch((e: { code: string }) => e.code);
      await vi.advanceTimersByTimeAsync(mod.T.api + 100);
      expect(await r).toBe("timeout");
    } finally { vi.useRealTimers(); }
  });

  it("приглашённые: обязательные и необязательные уходят на сервер; не прочитались — поля нет (сервер список не меняет); окно просмотра — готовый список", async () => {
    const item = G.Office.context.mailbox.item;
    item.requiredAttendees = { getAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value: [{ emailAddress: "bob@example.org", displayName: "Bob" }, { displayName: "без адреса" }] }) };
    item.optionalAttendees = { getAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value: [{ emailAddress: "guest@example.org", displayName: "" }] }) };
    expect((await mod.prepare(item)).payload.attendees).toEqual([{ email: "bob@example.org", name: "Bob", optional: false }, { email: "guest@example.org", name: "", optional: true }]);
    item.optionalAttendees = { getAsync: (cb: (r: unknown) => void) => cb({ status: "failed", error: { code: 1, message: "нет" } }) };
    expect("attendees" in (await mod.prepare(item)).payload).toBe(false);
    item.requiredAttendees = [{ emailAddress: "carol@example.org", displayName: "Carol" }]; item.optionalAttendees = [];
    expect((await mod.prepare(item)).payload.attendees).toEqual([{ email: "carol@example.org", name: "Carol", optional: false }]);
    await send();
    expect(JSON.parse(calls("/meetings").at(-1)![1].body).attendees).toEqual([{ email: "carol@example.org", name: "Carol", optional: false }]);
  });

  it("прошедшая встреча отклоняется до запроса к серверу понятной ошибкой", async () => {
    const item = G.Office.context.mailbox.item;
    item.start = { getAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value: new Date(Date.now() - 5 * 3600_000) }) };
    item.end = { getAsync: (cb: (r: unknown) => void) => cb({ status: "succeeded", value: new Date(Date.now() - 4 * 3600_000) }) };
    await expect(mod.prepare(item)).rejects.toMatchObject({ code: "in_past", stage: "meeting" });
    expect(calls("/meetings").length).toBe(0);
  });

  it("ссылку не удалось вставить: комната есть, ошибка частичная (invite_failed), повтор возвращает ту же комнату", async () => {
    bodyFails = true;
    const item = G.Office.context.mailbox.item;
    const p = await mod.prepare(item);
    const room = await mod.requestRoom(p.payload, "pgo_x");
    await expect(mod.applyInvite(item, room)).rejects.toMatchObject({ code: "invite_failed", stage: "invite" });
    bodyFails = false;
    expect(await mod.applyInvite(item, room)).toEqual({ body: true, location: true });
    expect(body.split("<!--pg-conf-->").length - 1).toBe(1);
  });

  it("клиент без поля «Место»: ссылка в тексте есть, место помечено как не изменённое", async () => {
    const item = G.Office.context.mailbox.item;
    item.location = { setAsync: (_v: string, cb: (r: unknown) => void) => cb({ status: "failed", error: { code: 1, message: "read-only" } }) };
    expect(await mod.applyInvite(item, ROOM)).toEqual({ body: true, location: false });
  });

  it("вход: токен из окна сохраняется; закрытие окна — «отменено»; токен истёк (401) — новый вход и повтор", async () => {
    delete store["pg_outlook_token"];
    const pending = mod.login();
    expect(dialog!.url).toBe("https://meet.example.org/outlook/auth.html");
    dialog!.reply({ token: "pgo_new", expires_in: 7200 });
    expect(await pending).toBe("pgo_new");
    expect(mod.readToken(G.localStorage, Date.now())).toBe("pgo_new");
    const cancelled = mod.login();
    dialog!.closeByUser();
    await expect(cancelled).rejects.toMatchObject({ code: "auth_cancelled" });
    const denied = mod.login();
    dialog!.reply({ error: "Сервер авторизации временно недоступен.", code: "auth_unavailable" });
    await expect(denied).rejects.toMatchObject({ code: "auth_unavailable" });
    // команда: сохранённый токен отозван → окно входа → повтор с новым токеном
    store["pg_outlook_token"] = JSON.stringify({ token: "pgo_old", exp: Date.now() + 3600_000 });
    fetchMock.mockImplementationOnce(async () => ({ status: 401, json: async () => ({ detail: { code: "token_invalid" } }) }));
    dialog = null;
    const done = press();
    await vi.waitFor(() => expect(dialog).not.toBeNull());
    dialog!.reply({ token: "pgo_fresh", expires_in: 3600 });
    await done;
    expect(calls("/meetings").map((c) => c[1].headers.Authorization)).toEqual(["Bearer pgo_old", "Bearer pgo_fresh"]);
    expect(location).toBe(ROOM.url);
  });

  it("отправка приглашения: встреча с конференцией синхронизируется, без неё — нет; отправку не блокирует никогда", async () => {
    expect(await send()).toBe(true);
    expect(calls("/meetings").length).toBe(0);
    props.pg_key = "k".repeat(20);
    expect(await send()).toBe(true);
    expect(JSON.parse(calls("/meetings")[0][1].body)).toMatchObject({ key: props.pg_key, ews_id: "EWS-ITEM-ID-123" });
  });

  it("отправка: сбой сервера, истёкший вход или нет токена не мешают отправить; окно входа не открывается", async () => {
    props.pg_key = "k".repeat(20);
    fetchMock.mockResolvedValueOnce({ status: 500, json: async () => ({}) });
    expect(await send()).toBe(true);
    fetchMock.mockRejectedValueOnce(new Error("offline"));
    expect(await send()).toBe(true);
    delete store["pg_outlook_token"];
    fetchMock.mockClear();
    expect(await send()).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(dialog).toBeNull();
  });

  it("ошибка уходит в журнал сервера с этапом, кодом и сведениями о клиенте", async () => {
    fetchMock.mockImplementation(async (url: string) => (url.endsWith("/log") ? { status: 204, json: async () => ({}) } : { status: 403, json: async () => ({ detail: { message: "Нельзя" } }) }));
    await press();
    const log = JSON.parse(calls("/log")[0][1].body);
    expect(log).toMatchObject({ where: "api", code: "forbidden", message: "Нельзя" });
    expect(typeof log.client).toBe("string");
  });

  it("вход в панели: токен сразу (без окна), пароль не сохраняется; ошибки — понятными кодами", async () => {
    delete store["pg_outlook_token"];
    fetchMock.mockImplementationOnce(async () => ({ status: 200, json: async () => ({ token: "pgo_panel", expires_in: 7 * 24 * 3600 }) }));
    expect(await mod.passwordLogin("alice", "secret")).toBe("pgo_panel");
    const req = calls("/login")[0][1];
    expect(req.headers.Authorization).toBeUndefined();
    expect(JSON.parse(req.body)).toEqual({ login: "alice", password: "secret" });
    expect(JSON.stringify(store)).not.toContain("secret");
    expect(mod.readToken(G.localStorage, Date.now())).toBe("pgo_panel");
    expect(dialog).toBeNull();
    const cases: [number, unknown, string][] = [
      [401, { code: "invalid_credentials" }, "wrong_password"], [429, { code: "throttled" }, "throttled"], [409, { code: "browser_required", reason: "totp" }, "browser_required"],
      [428, { code: "captcha_required" }, "browser_required"], [503, { code: "directory_unavailable" }, "auth_unavailable"], [403, "нет", "forbidden"], [404, "выкл", "disabled"],
    ];
    for (const [status, detail, code] of cases) {
      fetchMock.mockImplementationOnce(async () => ({ status, json: async () => ({ detail }) }));
      await expect(mod.passwordLogin("alice", "x")).rejects.toMatchObject({ code, stage: "auth" });
    }
    await expect(mod.passwordLogin("", "")).rejects.toMatchObject({ code: "no_credentials" });
    expect(mod.explain({ code: "wrong_password" }).text).toBe("Проверьте логин и пароль и попробуйте снова.");
  });

  it("продление входа: после половины срока — новый токен; сбой продления не мешает работать", async () => {
    const now = Date.now();
    mod.saveToken(G.localStorage, "pgo_a", 7 * 24 * 3600, now);
    expect(mod.needsRefresh(G.localStorage, now + 3 * 24 * 3600_000)).toBe(false);
    expect(mod.needsRefresh(G.localStorage, now + 4 * 24 * 3600_000)).toBe(true);
    expect(mod.needsRefresh(G.localStorage, now + 8 * 24 * 3600_000)).toBe(false);            // истёк — нужен вход, а не продление
    fetchMock.mockImplementationOnce(async () => ({ status: 200, json: async () => ({ token: "pgo_b", expires_in: 7 * 24 * 3600 }) }));
    expect(await mod.refresh("pgo_a")).toBe("pgo_b");
    expect(calls("/token/refresh")[0][1].headers.Authorization).toBe("Bearer pgo_a");
    expect(mod.readToken(G.localStorage, Date.now())).toBe("pgo_b");
    fetchMock.mockRejectedValueOnce(new Error("offline"));
    expect(await mod.refresh("pgo_b")).toBe("pgo_b");
  });
});
