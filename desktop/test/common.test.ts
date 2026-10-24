import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import { normalizeServerUrl, domainAllowed, safeRoute, externalAllowed, sameOrigin } from "../src/common/urls";
import { parseDeepLink, findDeepLinkArg, parseMeetingUrl } from "../src/common/links";
import { parseRegQuery, policyFromValues, mergePolicy } from "../src/common/policy";
import { fitBounds, MIN_SIZE } from "../src/common/bounds";
import { redact } from "../src/common/redact";
import { compat, cmpVersion } from "../src/common/version";
import { describeNetError, backoffMs } from "../src/common/neterr";
import { Dedupe, shapeNotification } from "../src/common/dedupe";
import { verifyManifest, newer, canRestartNow } from "../src/main/updateCore";

test("адрес сервера: только HTTPS, без учётных данных и пути, ввод без схемы", () => {
  assert.deepEqual(normalizeServerUrl("meet.example.org"), { origin: "https://meet.example.org" });
  assert.deepEqual(normalizeServerUrl(" https://Meet.Example.org:8443/ "), { origin: "https://meet.example.org:8443" });
  assert.deepEqual(normalizeServerUrl("http://meet.example.org"), { error: "not_https" });
  assert.deepEqual(normalizeServerUrl("https://user:pw@meet.example.org"), { error: "credentials" });
  assert.deepEqual(normalizeServerUrl("https://meet.example.org/rooms/x"), { error: "path" });
  assert.ok("error" in normalizeServerUrl("javascript:alert(1)"));
  assert.deepEqual(normalizeServerUrl("file:///C:/x"), { error: "not_https" });
  assert.deepEqual(normalizeServerUrl(""), { error: "empty" });
  assert.deepEqual(normalizeServerUrl("http://127.0.0.1:8000"), { error: "not_https" }, "HTTP для стенда — только по явному флагу разработчика");
  assert.deepEqual(normalizeServerUrl("http://127.0.0.1:8000", { allowHttpLoopback: true }), { origin: "http://127.0.0.1:8000" });
  assert.deepEqual(normalizeServerUrl("http://192.0.2.10", { allowHttpLoopback: true }), { error: "not_https" }, "флаг не открывает HTTP для сетевых адресов");
  assert.deepEqual(normalizeServerUrl("https://пример.рф"), { origin: "https://xn--e1afmkfd.xn--p1ai" });
});

test("тот же сервер и разрешённые домены", () => {
  assert.ok(sameOrigin("https://meet.example.org/rooms/a?x=1", "https://meet.example.org"));
  assert.ok(!sameOrigin("https://meet.example.org.evil.example/rooms", "https://meet.example.org"));
  assert.ok(!sameOrigin("https://meet.example.org:444/", "https://meet.example.org"));
  assert.ok(domainAllowed("meet.example.org", ["*.example.org"]));
  assert.ok(!domainAllowed("example.org", ["*.example.org"]), "маска — только поддомены");
  assert.ok(!domainAllowed("evilmeet.example", ["*.meet.example"]), "похожее имя без точки — не поддомен");
  assert.ok(domainAllowed("example.org", ["example.org"]) && domainAllowed("x.test", []));
});

test("маршрут команды и внешние ссылки", () => {
  assert.equal(safeRoute("/rooms/sales?x=1#a"), "/rooms/sales?x=1#a");
  for (const bad of ["//evil.example/x", "https://evil.example", "rooms", "/\\evil", "/a\u0000", 5, null]) assert.equal(safeRoute(bad), null, String(bad));
  assert.ok(externalAllowed("https://example.org/doc") && externalAllowed("mailto:a@example.org"));
  for (const bad of ["file:///C:/Windows/system32/calc.exe", "javascript:alert(1)", "ms-settings:privacy", "\\\\192.0.2.1\\share", "https://u:p@example.org"]) assert.ok(!externalAllowed(bad), bad);
});

test("ссылки peregovorka://: только сервер и разрешённый путь встречи", () => {
  const u = (s: string) => "peregovorka://open?url=" + encodeURIComponent(s);
  assert.deepEqual(parseDeepLink(u("https://meet.example.org/rooms/sales")), { origin: "https://meet.example.org", route: "/rooms/sales" });
  assert.deepEqual(parseDeepLink(u("https://meet.example.org/m/AbCd1234")), { origin: "https://meet.example.org", route: "/m/AbCd1234" });
  assert.deepEqual(parseDeepLink(u("https://meet.example.org/")), { origin: "https://meet.example.org", route: "/" });
  for (const bad of [u("http://meet.example.org/rooms/a"), u("https://meet.example.org/admin"), u("https://meet.example.org/rooms/a/../../admin"),
    u("https://meet.example.org/api/v1/auth/logout"), u("javascript:alert(1)"), "peregovorka://run?cmd=calc", "peregovorka://open?url=https%3A%2F%2Fa.example.org%2F&exec=1",
    "peregovorka://open/../x?url=" + encodeURIComponent("https://a.example.org/"), "https://meet.example.org/rooms/a", "peregovorka://open?url=" + "a".repeat(3000),
    u("https://u:p@meet.example.org/rooms/a"), u("https://meet.example.org/rooms/<script>")]) {
    assert.equal(parseDeepLink(bad), null, bad);
  }
  assert.equal(parseMeetingUrl("https://meet.example.org/rooms/sales/")?.route, "/rooms/sales");
  assert.equal(findDeepLinkArg(["C:\\app.exe", "--hidden", "peregovorka://open?url=x"]), "peregovorka://open?url=x");
  assert.equal(findDeepLinkArg(["C:\\app.exe"]), null);
  assert.equal(parseDeepLink(u("http://127.0.0.1:5180/rooms/a")), null);
  assert.deepEqual(parseDeepLink(u("http://127.0.0.1:5180/rooms/a"), { allowHttpLoopback: true }), { origin: "http://127.0.0.1:5180", route: "/rooms/a" }, "стенд — только по флагу");
  assert.equal(parseDeepLink(u("http://192.0.2.5/rooms/a"), { allowHttpLoopback: true }), null);
});

test("политики реестра: разбор, проверка значений, HKLM сильнее HKCU", () => {
  const out = `
HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\Peregovorka
    Servers    REG_SZ    Организация А|https://a.example.org;Тест|b.example.org;Плохой|http://c.example.org
    AllowAddServers    REG_DWORD    0x0
    AllowedDomains    REG_SZ    *.example.org;example.org
    Autostart    REG_SZ    Tray
    UpdateMode    REG_SZ    sometimes
    HideNotificationContent    REG_DWORD    0x1
    ProxyMode    REG_SZ    pac
    ProxyPac    REG_SZ    https://proxy.example.org/wpad.dat
`;
  const p = policyFromValues(parseRegQuery(out));
  assert.deepEqual(p.servers, [{ name: "Организация А", origin: "https://a.example.org" }, { name: "Тест", origin: "https://b.example.org" }], "HTTP-адрес в политике отброшен");
  assert.equal(p.allowAddServers, false);
  assert.deepEqual(p.allowedDomains, ["*.example.org", "example.org"]);
  assert.equal(p.autostart, "tray");
  assert.equal(p.updateMode, undefined, "неверное значение = не задано");
  assert.equal(p.hideContent, true);
  assert.deepEqual(p.proxy, { mode: "pac", rules: undefined, bypass: undefined, pac: "https://proxy.example.org/wpad.dat" });
  const user = policyFromValues(parseRegQuery("    Autostart    REG_SZ    off\n    Servers    REG_SZ    https://home.example.org\n    UpdateMode    REG_SZ    notify"));
  const m = mergePolicy(p, user);
  assert.equal(m.autostart, "tray");
  assert.equal(m.updateMode, "notify");
  assert.deepEqual(m.servers.map((s) => s.origin), ["https://a.example.org", "https://b.example.org", "https://home.example.org"]);
  assert.deepEqual(policyFromValues(parseRegQuery("ERROR: The system was unable to find the specified registry key or value.")), { servers: [] });
});

test("положение окна: на видимом мониторе, иначе по центру основного", () => {
  const primary = { x: 0, y: 0, width: 1920, height: 1040 };
  const second = { x: 1920, y: 0, width: 1280, height: 984 };
  assert.deepEqual(fitBounds({ x: 2000, y: 100, width: 1000, height: 700 }, [primary, second], primary), { x: 2000, y: 100, width: 1000, height: 700 });
  const gone = fitBounds({ x: 2000, y: 100, width: 1000, height: 700 }, [primary], primary);
  assert.deepEqual(gone, { x: 460, y: 170, width: 1000, height: 700 }, "второй монитор отключён — окно вернулось на основной");
  const big = fitBounds({ x: 1900, y: -5, width: 3000, height: 2000 }, [primary, second], primary);
  assert.ok(big.width <= 1920 && big.height <= 1040 && big.y >= 0);
  const small = fitBounds({ x: 10, y: 10, width: 200, height: 100 }, [primary], primary);
  assert.ok(small.width >= MIN_SIZE.width && small.height >= MIN_SIZE.height);
  const first = fitBounds(null, [primary], primary);
  assert.ok(first.x >= 0 && first.x + first.width <= 1920);
  const tiny = { x: 0, y: 0, width: 1024, height: 728 };
  const f2 = fitBounds(undefined, [tiny], tiny);
  assert.ok(f2.width <= 1024 && f2.height <= 728, JSON.stringify(f2));
});

test("маскирование секретов в журнале и отчёте", () => {
  const s = redact('token=pgd_abcdefghijklmnop Cookie: vm_session=SeCrEt123; csrf_token":"zzz" Authorization: Bearer abc.def https://x.example.org/guest/ABCDEFGHIJKL?key=777&a=1 password=hunter2');
  for (const leak of ["pgd_abcdefghijklmnop", "SeCrEt123", "zzz", "abc.def", "ABCDEFGHIJKL", "777", "hunter2"]) assert.ok(!s.includes(leak), `${leak} в ${s}`);
  assert.ok(s.includes("a=1"));
});

test("совместимость клиента и сервера", () => {
  assert.equal(cmpVersion("1.10.0", "1.9.9"), 1);
  assert.equal(cmpVersion("1.0.0", "1.0.0"), 0);
  assert.deepEqual(compat({ product: "peregovorka", api: 1, min_client: "1.0.0" }, "1.0.0"), { ok: true });
  assert.equal((compat({ product: "peregovorka", api: 2, min_client: "1.0.0" }, "1.0.0") as { code: string }).code, "server_too_new");
  assert.equal((compat({ product: "peregovorka", api: 1, min_client: "1.2.0" }, "1.1.9") as { code: string }).code, "client_too_old");
  assert.equal((compat(null, "1.0.0") as { code: string }).code, "not_peregovorka");
});

test("ошибки сети — понятным языком; переподключение с нарастающей паузой", () => {
  assert.equal(describeNetError(new Error("net::ERR_CERT_AUTHORITY_INVALID")).title, "Сертификат не доверен");
  assert.equal(describeNetError("net::ERR_NAME_NOT_RESOLVED").kind, "dns");
  assert.equal(describeNetError("net::ERR_CONNECTION_REFUSED").title, "Сервер недоступен");
  assert.equal(describeNetError("net::ERR_TUNNEL_CONNECTION_FAILED").kind, "proxy");
  assert.equal(describeNetError("boom").kind, "other");
  assert.ok(backoffMs(0, 0.5) === 2000 && backoffMs(3, 0.5) === 16000 && backoffMs(50, 1) <= 360000 && backoffMs(50, 0) >= 240000);
});

test("уведомления: без повторов, «Не беспокоить», скрытие содержимого", () => {
  const d = new Dedupe(1000, 10);
  assert.ok(d.fresh("a:chat:1:5", 0));
  assert.ok(!d.fresh("a:chat:1:5", 500), "повтор того же события после переподключения не показывается");
  assert.ok(d.fresh("b:chat:1:5", 500), "другая организация — другое событие");
  assert.ok(d.fresh("a:chat:1:5", 1600), "через срок — снова можно");
  for (let i = 0; i < 50; i++) d.fresh(`k${i}`, 2000);
  assert.ok((d as unknown as { seen: Map<string, number> }).seen.size <= 10);
  const p = { enabled: true, sound: true, hideContent: false, dndUntil: 0 };
  assert.deepEqual(shapeNotification({ title: "Анна", body: "Привет", kind: "chat" }, p), { title: "Анна", body: "Привет", silent: false });
  assert.equal(shapeNotification({ title: "Анна", kind: "chat" }, { ...p, dndUntil: -1 }), null);
  assert.equal(shapeNotification({ title: "Анна", kind: "chat" }, { ...p, dndUntil: 2000 }, 1000), null);
  assert.ok(shapeNotification({ title: "Анна", kind: "chat" }, { ...p, dndUntil: 500 }, 1000));
  assert.deepEqual(shapeNotification({ title: "Анна", body: "Секрет", kind: "chat" }, { ...p, hideContent: true, sound: false }), { title: "Новое сообщение", body: "Откройте Peregovorka, чтобы прочитать", silent: true });
  assert.equal(shapeNotification({ title: "x", kind: "chat" }, { ...p, enabled: false }), null);
});

test("обновления: только подписанный манифест издателя, проверка полей", () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("base64");
  const body = Buffer.from(JSON.stringify({ product: "peregovorka-desktop", version: "1.2.0", released: "2026-10-24", files: [{ kind: "nsis", name: "Peregovorka-Setup-1.2.0.exe", url: "https://github.com/x/y/releases/download/desktop-v1.2.0/Peregovorka-Setup-1.2.0.exe", sha256: "a".repeat(64), size: 100 }] }));
  const sig = sign(null, body, privateKey).toString("base64");
  const m = verifyManifest(body, sig, raw);
  assert.ok(newer(m, "1.1.9") && !newer(m, "1.2.0"));
  assert.throws(() => verifyManifest(Buffer.concat([body, Buffer.from(" ")]), sig, raw), /bad_signature/, "изменённый манифест");
  const other = generateKeyPairSync("ed25519").publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("base64");
  assert.throws(() => verifyManifest(body, sig, other), /bad_signature/, "чужой ключ");
  assert.throws(() => verifyManifest(body, sig, ""), /no_key/, "сборка без ключа обновлений не устанавливает ничего");
  const evil = Buffer.from(JSON.stringify({ product: "peregovorka-desktop", version: "9.9.9", released: "x", files: [{ kind: "nsis", name: "a.exe", url: "http://evil.example/a.exe", sha256: "a".repeat(64), size: 1 }] }));
  assert.throws(() => verifyManifest(evil, sign(null, evil, privateKey).toString("base64"), raw), /bad_manifest/, "даже подписанный, без HTTPS — отказ");
  assert.ok(canRestartNow([{ inCall: false, sharing: false }]) && !canRestartNow([{ inCall: false, sharing: false }, { inCall: true, sharing: false }]));
});
