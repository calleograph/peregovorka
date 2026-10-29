// Главный процесс Peregovorka для Windows. Схема доверия:
//  • app://shell/* — доверенная локальная оболочка из пакета приложения (боковая панель, мастер, настройки, диагностика, выбор экрана) — preload-shell;
//  • страницы серверов организаций — недоверенное удалённое содержимое в отдельных разделах, песочница, мост preload-org с тремя действиями;
//  • каждая IPC-команда проверяет отправителя: окно, главный фрейм и адрес (для организации — её origin).
import { app, BrowserWindow, dialog, ipcMain, nativeImage, nativeTheme, net, Notification, powerMonitor, protocol, safeStorage, session, shell, systemPreferences, type IpcMainEvent, type IpcMainInvokeEvent, type WebContents } from "electron";
import { execFile } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { findDeepLinkArg, parseDeepLink } from "../common/links";
import { SOUND_SETTINGS, soundCommand, type SoundSettings } from "../common/media";
import { shellFile } from "../common/shellfile";
import { normalizeServerUrl, safeRoute, sameOrigin, externalAllowed } from "../common/urls";
import type { Ctx } from "./ctx";
import { collect, type Devices } from "./diagnostics";
import { Log } from "./log";
import { Notifier } from "./notify";
import { Orgs, type Org, type PageState } from "./orgs";
import { Indicator, ScreenPicker } from "./picker";
import { probe } from "./probe";
import { effective, loadPolicy, lockedFields, SettingsStore } from "./settings";
import { contextMenu, handleDownload, openDownloaded } from "./system";
import { applyAutostart, AppTray } from "./tray";
import { TrustStore } from "./trust";
import { Updater } from "./updater";
import { MainWindow, themeBg } from "./window";

declare const __APP_VERSION__: string;
declare const __BUILD_KIND__: string;

const T0 = Date.now();
const dev = !app.isPackaged;
const argv = process.argv;
const flag = (f: string) => argv.includes(f);
if (dev && process.env.PEREGOVORKA_USER_DATA) app.setPath("userData", process.env.PEREGOVORKA_USER_DATA);       // испытания: свой каталог данных
app.setAppUserModelId("org.peregovorka.desktop");                   // совпадает с appId установщика: иначе Windows не показывает уведомления приложения
protocol.registerSchemesAsPrivileged([{ scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  void boot();
}

async function boot(): Promise<void> {
  await app.whenReady();
  // испытания под Linux без связки ключей: шифрование-заглушка Chromium (в Windows всегда DPAPI); только запуск из исходников с --e2e
  if (dev && flag("--e2e") && process.platform === "linux") safeStorage.setUsePlainTextEncryption(true);
  // режим диагностики запуска: журнал главного процесса, ошибки preload и консоль оболочки — ещё и в терминал (npm run dev, проверки перед выпуском)
  const debugLog = flag("--debug-log") || !!process.env.PEREGOVORKA_DEBUG;
  if (debugLog) process.env.PEREGOVORKA_LOG_STDERR = "1";
  const userData = app.getPath("userData");
  const log = new Log(join(userData, "logs"));
  process.on("uncaughtException", (e) => log.error("main.exception", { error: String(e?.stack ?? e).slice(0, 1500) }));
  process.on("unhandledRejection", (e) => log.error("main.rejection", { error: String((e as Error)?.stack ?? e).slice(0, 1500) }));
  const settings = new SettingsStore(userData);
  const policy = await loadPolicy();
  const ctx: Ctx = {
    version: __APP_VERSION__, buildKind: __BUILD_KIND__, install: app.isPackaged ? "exe" : "dev", userData, log, settings, policy,
    trust: new TrustStore(userData, dev && flag("--e2e")),
    eff: () => effective(settings.get(), policy),
    flags: { allowHttpLoopback: dev && flag("--allow-http-loopback"), e2e: dev && flag("--e2e"), hidden: flag("--hidden"), devTools: dev || policy.allowDevTools === true },
    quitting: false,
    emit: () => scheduleEmit(),
  };
  nativeTheme.themeSource = ctx.eff().theme;
  log.info("app.start", { version: ctx.version, build: ctx.buildKind, electron: process.versions.electron, packaged: app.isPackaged,
    ...(debugLog ? { userData, app: app.getAppPath(), shell: existsSync(join(__dirname, "shell", "index.html")) } : {}) });

  const bridgeReady = new Set<number>();          // страницы, где интерфейс сервера уже слушает команды приложения

  // ---------------------------------------------------------------- доверенная оболочка: app://shell/
  const root = join(__dirname, "shell");
  const CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'";
  const TYPES: Record<string, string> = { html: "text/html; charset=utf-8", js: "text/javascript; charset=utf-8", css: "text/css; charset=utf-8", woff2: "font/woff2", png: "image/png", svg: "image/svg+xml" };
  protocol.handle("app", async (req) => {
    const f = shellFile(root, req.url);
    const r = f ? await net.fetch(f.url).catch((e) => { log.error("shell.read_failed", { error: String(e).slice(0, 200) }); return null; }) : null;
    if (!f || !r || !r.ok) {
      log.error("shell.not_found", { path: new URL(req.url).pathname.slice(0, 100), status: r?.status ?? null });
      return new Response("not found", { status: 404 });
    }
    return new Response(r.body, { headers: { "content-type": TYPES[f.file.split(".").pop() || ""] || "application/octet-stream", "content-security-policy": CSP, "x-content-type-options": "nosniff" } });
  });
  // раздел по умолчанию принадлежит только оболочке: никаких разрешений и загрузок с удалённых адресов
  // Единственное исключение — микрофон, камера и выбор динамиков для проверки оборудования в настройках: только главное окно оболочки и только с адреса app://shell
  // (отдельная от организаций сессия: страницы серверов этих разрешений здесь не получают).
  const shellMedia = (wc: WebContents | null, perm: string, url: string): boolean =>
    (perm === "media" || perm === "speaker-selection") && !!wc && !!main?.win && !main.win.isDestroyed() && wc.id === main.win.webContents.id && url.startsWith("app://shell/");
  session.defaultSession.setPermissionRequestHandler((wc, perm, cb, d) => cb(shellMedia(wc, perm, d.requestingUrl)));
  session.defaultSession.setPermissionCheckHandler((wc, perm, origin) => shellMedia(wc, perm, origin.endsWith("/") ? origin : `${origin}/`));
  // Служебные процессы Chromium (в том числе «Audio Service»: захват микрофона и воспроизведение): если процесс упал, захват звука прерывается и страница видит «микрофон пропал».
  // Без записи об этом причину не восстановить, поэтому каждый сбой — в журнал (виден во вкладке «Диагностика»).
  app.on("child-process-gone", (_e, d) => log.error("child.gone", { type: d.type, name: (d.name ?? d.serviceName ?? "").slice(0, 60), reason: d.reason, exitCode: d.exitCode }));
  app.on("web-contents-created", (_e, wc) => {
    wc.on("will-attach-webview", (e) => e.preventDefault());
    wc.on("preload-error", (_ev, path, err) => log.error("preload.error", { preload: path.split(/[\\/]/).pop(), error: String(err?.stack ?? err).slice(0, 600) }));
    // консоль оболочки (файлы приложения): предупреждения и ошибки — всегда в журнал; страницы серверов — только ошибки и только в режиме диагностики
    wc.on("console-message", (d) => {
      const own = wc.session === session.defaultSession;
      if (!(own ? d.level === "error" || d.level === "warning" || debugLog : debugLog && d.level === "error")) return;
      log.write(d.level === "error" ? "error" : d.level === "warning" ? "warn" : "info", own ? "shell.console" : "page.console",
        { level: d.level, message: d.message.slice(0, 300), source: (d.sourceId || "").replace(/^app:\/\/shell\//, "").slice(0, 120), line: d.lineNumber });
    });
    wc.on("did-navigate", () => bridgeReady.delete(wc.id));             // полная перезагрузка страницы: мост объявит готовность заново
    if (wc.session === session.defaultSession) {
      wc.setWindowOpenHandler(() => ({ action: "deny" }));
      wc.on("will-navigate", (e, url) => { if (!url.startsWith("app://shell/")) e.preventDefault(); });
    }
  });

  // ---------------------------------------------------------------- окна и организации
  let orgs!: Orgs;
  const main = new MainWindow(ctx, () => orgs);
  const picker = new ScreenPicker(ctx, () => main.win);
  const indicator = new Indicator(ctx);
  const notifier = new Notifier(ctx, (o, route) => { focusOrg(o); if (route) navigate(o, route); });
  const childWindows = new Set<BrowserWindow>();
  orgs = new Orgs(ctx, {
    attach: (v) => main.attach(v),
    detach: (v) => main.detach(v),
    layout: () => main.layout(),
    pickScreen: (o, req, cb) => void picker.open(o, req, cb),
    notify: (o, n) => notifier.show(o, n, orgs.list.length > 1),
    confirmReloadInCall: () => dialog.showMessageBoxSync(main.win, { type: "warning", buttons: ["Обновить страницу", "Отмена"], defaultId: 1, cancelId: 1, title: "Peregovorka",
      message: "Вы участвуете во встрече", detail: "Обновление страницы прервёт подключение к встрече." }) === 0,
    download: (o, item) => handleDownload(ctx, item, (name, path, ok) => {
      notifier.show(o, { tag: `dl:${path}:${Date.now()}`, title: ok ? "Загрузка завершена" : "Загрузка прервана", body: ok ? `${name} — нажмите, чтобы открыть` : name, kind: "info",
        open: ok ? () => openDownloaded(path) : undefined }, false);
      if (ok) lastDownload = path;
    }),
    contextMenu,
    childWindowOptions: (o) => ({ width: 1200, height: 800, minWidth: 640, minHeight: 480, autoHideMenuBar: true, backgroundColor: themeBg(), icon: join(__dirname, "assets", "icon.png"),
      webPreferences: orgs.webPreferences(o) }),
    windowCreated: (_o, w) => { w.setMenu(null); childWindows.add(w); w.on("closed", () => childWindows.delete(w)); },
  });
  let lastDownload: string | null = null;
  orgs.load();

  const managedInstall = await isMsiInstall();
  if (managedInstall) ctx.install = "msi";
  const updater = new Updater(ctx, () => orgs.list.map((o) => o.state), managedInstall, () => ctx.emit(), (version) => {
    const n = ctx.eff().notifications;
    if (!n.enabled || n.dndUntil === -1 || n.dndUntil > Date.now() || !Notification.isSupported()) return;
    const note = new Notification({ title: "Доступна новая версия Peregovorka", body: `Версия ${version}. Скачать и установить — «Настройки → Обновления».`, silent: true });
    note.on("click", () => openSettings("updates"));
    note.show();
  });

  // ---------------------------------------------------------------- состояние для оболочки, трея, значка, индикатора
  let emitTimer: NodeJS.Timeout | null = null;
  function scheduleEmit(): void {
    if (emitTimer) return;
    emitTimer = setTimeout(() => { emitTimer = null; emitNow(); }, 50);
  }
  function shellState() {
    const s = ctx.eff();
    return {
      version: ctx.version, buildKind: ctx.buildKind, platform: process.platform, activeId: orgs.activeId, page: main.page,
      orgs: orgs.list.map((o) => ({ id: o.rec.id, name: o.rec.name, origin: o.rec.origin, host: new URL(o.rec.origin).host, managed: o.rec.managed, icon: o.rec.icon ?? null,
        status: o.status, problem: o.problem ? { title: o.problem.title, hint: o.problem.hint, code: o.problem.code } : null, retryAt: o.retryAt || null, user: o.user,
        unread: o.state.unread, inCall: o.state.inCall, sharing: o.state.sharing, theme: o.state.theme, trusted: !!ctx.trust.get(o.rec.id), serverVersion: o.info?.server_version ?? null,
        trustDays: o.info?.trust_days ?? null })),
      settings: s, locked: lockedFields(policy), canAdd: orgs.canAdd().ok, allowedDomains: policy.allowedDomains ?? [], update: updater.state, trustAvailable: ctx.trust.available(),
    };
  }
  function emitNow(): void {
    if (main.win.isDestroyed()) return;
    main.win.webContents.send("shell:state", shellState());
    tray?.update(orgs.list, orgs.activeId);
    main.layout();
    const call = orgs.list.find((o) => o.state.inCall || o.state.sharing);
    const hidden = !main.win.isVisible() || main.win.isMinimized();
    indicator.update(call && (call.state.sharing || hidden) ? { org: call.rec.name, sharing: call.state.sharing, inCall: call.state.inCall, mic: call.state.mic } : null);
  }
  for (const ev of ["show", "hide", "minimize", "restore", "focus"] as const) main.win.on(ev as "show", () => scheduleEmit());

  function focusOrg(o: Org): void {
    main.reveal();
    main.showPage("org");
    orgs.activate(o.rec.id);
  }
  function navigate(o: Org, route: string): void {
    const r = safeRoute(route);
    if (!r) return;
    const wc = o.view?.webContents;
    if (wc && !wc.isDestroyed() && o.status === "online" && bridgeReady.has(wc.id)) orgs.command(o, { type: "navigate", route: r });
    else if (wc && !wc.isDestroyed()) void wc.loadURL(o.rec.origin + r).catch(() => undefined);
  }

  // ---------------------------------------------------------------- IPC: проверка отправителя
  function shellSender(e: IpcMainEvent | IpcMainInvokeEvent, role: "main" | "picker" | "indicator"): boolean {
    const w = role === "main" ? main.win : role === "picker" ? picker.win : indicator.win;
    const f = e.senderFrame;
    const ok = !!w && !w.isDestroyed() && e.sender.id === w.webContents.id && !!f && f === e.sender.mainFrame && f.url.startsWith("app://shell/");
    if (!ok) log.warn("ipc.rejected", { role, url: (f?.url || "").slice(0, 80) });
    return ok;
  }
  function orgSender(e: IpcMainEvent): Org | null {
    const o = orgs.byContents(e.sender);
    const f = e.senderFrame;
    if (!o || !f || f !== e.sender.mainFrame || !sameOrigin(f.url, o.rec.origin)) { log.warn("ipc.org_rejected", { url: (f?.url || "").slice(0, 80) }); return null; }
    return o;
  }
  const handle = (ch: string, role: "main" | "picker" | "indicator", fn: (e: IpcMainInvokeEvent, a: Record<string, unknown>) => unknown) =>
    ipcMain.handle(ch, (e, a) => (shellSender(e, role) ? fn(e, (a && typeof a === "object" ? a : {}) as Record<string, unknown>) : null));
  const str = (v: unknown, n: number) => (typeof v === "string" ? v.slice(0, n) : "");

  handle("shell:state", "main", () => shellState());
  handle("shell:probe", "main", async (_e, a) => {
    const n = normalizeServerUrl(str(a.url, 400), { allowHttpLoopback: ctx.flags.allowHttpLoopback });
    if ("error" in n) return { ok: false, inputError: n.error };
    const can = orgs.canAdd(n.origin);
    if (!can.ok) return { ok: false, origin: n.origin, policyError: can.reason };
    if (orgs.byOrigin(n.origin)) return { ok: false, origin: n.origin, duplicate: true };
    return await probe(n.origin, ctx.version);
  });
  handle("shell:add", "main", async (_e, a) => {
    const n = normalizeServerUrl(str(a.url, 400), { allowHttpLoopback: ctx.flags.allowHttpLoopback });
    if ("error" in n || !orgs.canAdd(n.origin).ok) return { ok: false };
    const p = await probe(n.origin, ctx.version);
    if (!p.ok) return p;
    const o = orgs.add(str(a.name, 60), n.origin, p.info);
    settings.patch({ firstRunDone: true });
    main.showPage("org");
    const route = safeRoute(a.route);
    if (route && route !== "/") setTimeout(() => navigate(o, route), 0);
    return { ok: true, id: o.rec.id };
  });
  handle("shell:activate", "main", (_e, a) => { const o = orgs.byId(a.id); if (o) { orgs.activate(o.rec.id); main.showPage("org"); } });
  handle("shell:page", "main", (_e, a) => { main.showPage(str(a.page, 20)); scheduleEmit(); });
  handle("shell:rename", "main", (_e, a) => orgs.rename(str(a.id, 64), str(a.name, 60)));
  handle("shell:move", "main", (_e, a) => orgs.move(str(a.id, 64), a.dir === -1 ? -1 : 1));
  handle("shell:remove", "main", async (_e, a) => {
    const o = orgs.byId(a.id);
    if (!o) return false;
    const r = await dialog.showMessageBox(main.win, { type: "warning", buttons: ["Удалить подключение", "Отмена"], defaultId: 1, cancelId: 1, title: "Peregovorka",
      message: `Удалить подключение «${o.rec.name}»?`, detail: "Будет выполнен выход, сохранённый вход и данные этой организации на компьютере удалятся. Переписка на сервере не изменится." });
    return r.response === 0 ? orgs.remove(o.rec.id) : false;
  });
  handle("shell:signout", "main", (_e, a) => orgs.signOut(str(a.id, 64)));
  handle("shell:signoutAll", "main", async () => {
    const r = await dialog.showMessageBox(main.win, { type: "question", buttons: ["Выйти везде", "Отмена"], defaultId: 1, cancelId: 1, title: "Peregovorka", message: "Выйти из всех организаций?" });
    if (r.response === 0) await orgs.signOutAll();
  });
  handle("shell:reconnect", "main", (_e, a) => orgs.reconnect(str(a.id, 64)));
  handle("shell:settings", "main", async (_e, a) => {
    const before = ctx.eff();
    settings.patch(a);
    const s = ctx.eff();
    if (s.autostart !== before.autostart) applyAutostart(s.autostart, managedInstall && !!policy.autostart);
    if (JSON.stringify(s.proxy) !== JSON.stringify(before.proxy)) await orgs.applyProxyAll();
    if (s.updateMode !== before.updateMode) updater.schedule();
    if (s.theme !== before.theme) nativeTheme.themeSource = s.theme;
    scheduleEmit();
    return s;
  });
  handle("shell:update:check", "main", () => updater.check(true));
  handle("shell:update:download", "main", () => updater.download());
  handle("shell:update:install", "main", (_e, a) => {
    const now = a.now === true;
    if (now && !updater.canRestart()) return { ok: false, reason: "Установка будет доступна после завершения встречи." };
    return { ok: updater.install(now) };
  });
  handle("shell:diag", "main", (_e, a) => collect(ctx, orgs, updater.state, devicesFrom(a.devices), startupMs));
  handle("shell:diag:export", "main", async (_e, a) => {
    const r = await dialog.showSaveDialog(main.win, { title: "Сохранить отчёт", defaultPath: join(app.getPath("documents"), `Peregovorka-диагностика-${new Date().toISOString().slice(0, 10)}.json`),
      filters: [{ name: "Отчёт", extensions: ["json"] }] });
    if (r.canceled || !r.filePath) return false;
    writeFileSync(r.filePath, JSON.stringify(collect(ctx, orgs, updater.state, devicesFrom(a.devices), startupMs), null, 1), "utf8");
    shell.showItemInFolder(r.filePath);
    return true;
  });
  handle("shell:logs", "main", () => shell.openPath(log.dir));
  // Звук и видео: состояние разрешений Windows и открытие системных настроек (фиксированный перечень, без параметров от страницы)
  const access = (k: "microphone" | "camera") => { try { return systemPreferences.getMediaAccessStatus(k); } catch { return "unknown"; } };
  const SETTINGS_URIS = new Set(["ms-settings:sound", "ms-settings:privacy-microphone", "ms-settings:privacy-webcam"]);          // закрытый перечень: произвольный адрес через эту функцию открыть нельзя
  function openSettingsUri(u: string): void { if (SETTINGS_URIS.has(u)) void shell.openExternal(u); }
  function openSound(kind: string): { ok: boolean; reason?: string } {
    if (process.platform !== "win32") return { ok: false, reason: "windows_only" };
    if (!(SOUND_SETTINGS as readonly string[]).includes(kind)) return { ok: false, reason: "unknown_kind" };
    const c = soundCommand(kind as SoundSettings);
    log.info("sound.open", { kind });
    if (c.uri) { openSettingsUri(c.uri); return { ok: true }; }
    execFile(c.exe, c.args, { windowsHide: false }, (err) => { if (err && c.fallback) { log.warn("sound.open_fallback", { kind, error: String(err.message).slice(0, 120) }); openSettingsUri(c.fallback); } });
    return { ok: true };
  }
  handle("shell:av:state", "main", () => ({ platform: process.platform, mic: access("microphone"), camera: access("camera") }));
  handle("shell:sound:open", "main", (_e, a) => openSound(str(a.kind, 20)));
  handle("shell:external", "main", (_e, a) => { const u = str(a.url, 2000); if (externalAllowed(u)) void shell.openExternal(u); });
  handle("shell:badge", "main", (_e, a) => {
    if (process.platform !== "win32") return;
    const d = str(a.png, 20000);
    main.win.setOverlayIcon(d.startsWith("data:image/png;base64,") ? nativeImage.createFromDataURL(d) : null, str(a.label, 80));
  });
  handle("shell:picker:data", "picker", () => picker.data());
  handle("shell:picker:choose", "picker", (_e, a) => picker.finish(typeof a.id === "string" ? a.id : null, a.audio === true));
  handle("shell:indicator:data", "indicator", () => indicator.info);
  handle("shell:indicator:act", "indicator", (_e, a) => {
    const call = orgs.list.find((o) => o.state.inCall || o.state.sharing);
    if (!call) return;
    if (a.act === "open") focusOrg(call);
    else if (a.act === "stop-share") orgs.command(call, { type: "stop-share" });
    else if (a.act === "leave") orgs.command(call, { type: "leave-meeting" });
  });

  // мост страницы организации: уведомление, состояние, готовность к командам
  ipcMain.on("org:notify", (e, n) => {
    const o = orgSender(e);
    if (!o || !n || typeof n !== "object") return;
    const kind = ["chat", "mention", "meeting", "info"].includes(n.kind) ? n.kind : "info";
    const shown = notifier.show(o, { tag: str(n.tag, 200) || `${Date.now()}`, title: str(n.title, 200) || o.rec.name, body: str(n.body, 500), route: safeRoute(n.route) ?? undefined, kind },
      orgs.list.length > 1);
    if (shown && !main.win.isFocused()) main.win.flashFrame(true);
  });
  ipcMain.on("org:state", (e, s) => {
    const o = orgSender(e);
    if (!o || !s || typeof s !== "object") return;
    const p = o.pages.get(e.sender.id);
    if (!p) return;
    const next: PageState = { ...p };
    if (Number.isInteger(s.unread) && s.unread >= 0) next.unread = Math.min(s.unread, 99999);
    for (const k of ["inCall", "sharing", "mic"] as const) if (typeof s[k] === "boolean") next[k] = s[k];
    if (s.theme === "light" || s.theme === "dark") next.theme = s.theme;
    if (typeof s.ws === "string") next.ws = s.ws.slice(0, 20);
    o.pages.set(e.sender.id, next);
    scheduleEmit();
  });
  ipcMain.on("org:ready", (e) => { if (orgSender(e)) bridgeReady.add(e.sender.id); });
  // значения по умолчанию для выбора устройств и обработки звука на странице организации; открыть системные настройки звука из сообщения об ошибке микрофона
  ipcMain.handle("org:media:get", (e) => (orgSender(e as unknown as IpcMainEvent) ? ctx.eff().media : null));
  ipcMain.handle("org:sound:open", (e, a) => (orgSender(e as unknown as IpcMainEvent) ? openSound(str((a as Record<string, unknown> | null)?.kind, 20)) : { ok: false, reason: "rejected" }));
  main.win.webContents.on("did-finish-load", () => { scheduleEmit(); setTimeout(() => void checkShell(), 3000); });
  main.win.webContents.on("did-fail-load", (_e, code, desc, url, isMain) => { if (isMain) { log.error("shell.load_failed", { code, desc, url: url.slice(0, 80) }); void shellBroken(); } });
  main.win.webContents.on("render-process-gone", (_e, d) => { log.error("shell.gone", { reason: d.reason }); void shellBroken(); });

  // ---------------------------------------------------------------- оболочка не отрисовалась: окно не остаётся пустым
  async function shellHealth(): Promise<{ ok: boolean; url: string; heading: string; error?: string }> {
    const wc = main.win.webContents;
    const url = wc.getURL().slice(0, 80);
    try {
      const r = (await wc.executeJavaScript(`({ n: document.querySelector("#app main")?.childElementCount ?? 0, h: document.querySelector("#app h1")?.textContent ?? "" })`)) as { n: number; h: string };
      return { ok: url.startsWith("app://shell/") && r.n > 0, url, heading: r.h.slice(0, 80) };
    } catch (e) { return { ok: false, url, heading: "", error: String(e).slice(0, 120) }; }
  }
  let recovering = false;
  async function checkShell(): Promise<void> {
    // медленный компьютер (первый запуск, проверка антивирусом) рисует дольше: ошибка — только если окно пусто и через 15 с
    for (let i = 0; i < 6; i++) {
      const r = await shellHealth();
      if (r.ok) return;
      if (i === 5) { log.error("shell.blank", r); void shellBroken(); return; }
      await new Promise((res) => setTimeout(res, 2500));
    }
  }
  async function shellBroken(): Promise<void> {
    if (recovering || ctx.quitting || main.win.isDestroyed()) return;
    recovering = true;
    main.reveal();
    const r = await dialog.showMessageBox(main.win, { type: "error", buttons: ["Повторить", "Папка журналов", "Выйти"], defaultId: 0, cancelId: 0, title: "Peregovorka",
      message: "Не удалось открыть окно приложения", detail: "Файлы программы не загрузились. Повторите попытку; если ошибка повторяется — переустановите Peregovorka и передайте журналы администратору." });
    recovering = false;
    if (r.response === 1) { void shell.openPath(log.dir); void shellBroken(); }
    else if (r.response === 2) { ctx.quitting = true; app.quit(); }
    else void main.win.loadURL("app://shell/index.html").catch(() => undefined);
  }
  main.win.on("focus", () => main.win.flashFrame(false));

  // ---------------------------------------------------------------- закрытие и выход
  let toldAboutTray = false;
  async function requestQuit(): Promise<void> {
    const call = orgs.list.find((o) => o.state.inCall);
    if (call) {
      const r = await dialog.showMessageBox(main.win, { type: "warning", buttons: ["Выйти и покинуть встречу", "Отмена"], defaultId: 1, cancelId: 1, title: "Peregovorka",
        message: "Вы участвуете во встрече", detail: `Встреча в «${call.rec.name}» будет покинута.` });
      if (r.response !== 0) return;
    }
    ctx.quitting = true;
    app.quit();
  }
  async function toTray(): Promise<void> {
    const call = orgs.list.find((o) => o.state.inCall);
    if (call) {
      const r = await dialog.showMessageBox(main.win, { type: "question", buttons: ["Свернуть — встреча продолжится", "Покинуть встречу и свернуть", "Отмена"], defaultId: 0, cancelId: 2,
        title: "Peregovorka", message: "Вы участвуете во встрече", detail: call.state.mic ? "Микрофон включён: собеседники продолжат вас слышать. Поверх окон останется индикатор встречи." : "Поверх окон останется индикатор встречи." });
      if (r.response === 2) return;
      if (r.response === 1) orgs.command(call, { type: "leave-meeting" });
    }
    main.win.hide();
    if (!toldAboutTray && !settings.get().firstRunDone) {
      toldAboutTray = true;
      notifier.show(orgs.active ?? orgs.list[0], { tag: "tray-hint", title: "Peregovorka работает в фоне", body: "Сообщения и уведомления будут приходить. Открыть — значок в области уведомлений.", kind: "info" }, false);
    }
  }
  main.win.on("close", (e) => {
    if (ctx.quitting) { main.saveBounds(); return; }
    e.preventDefault();
    const action = ctx.eff().closeAction;
    if (action === "tray") void toTray();
    else if (action === "quit") void requestQuit();
    else {
      void dialog.showMessageBox(main.win, { type: "question", buttons: ["Свернуть в трей", "Выйти", "Отмена"], defaultId: 0, cancelId: 2, title: "Peregovorka",
        message: "Закрыть Peregovorka?", detail: "В трее приложение продолжит получать сообщения и уведомления.", checkboxLabel: "Запомнить выбор" }).then((r) => {
        if (r.response === 2) return;
        if (r.checkboxChecked && !policy.closeAction) settings.patch({ closeAction: r.response === 0 ? "tray" : "quit" });
        if (r.response === 0) void toTray(); else void requestQuit();
      });
    }
  });
  main.win.on("session-end", () => { ctx.quitting = true; });
  app.on("before-quit", () => { ctx.quitting = true; main.saveBounds(); updater.onQuit(); log.info("app.quit"); });
  app.on("window-all-closed", () => { /* работает в трее; выход — только явной командой */ });

  // ---------------------------------------------------------------- трей, ссылки приложения, второй запуск
  const tray = new AppTray({
    open: () => { main.reveal(); },
    chats: () => { const o = orgs.active; if (o) { focusOrg(o); orgs.command(o, { type: "chats" }); } },
    upcoming: () => { const o = orgs.active; if (o) { focusOrg(o); navigate(o, "/"); } },
    call: (o) => focusOrg(o),
    switchTo: (o) => focusOrg(o),
    settings: () => openSettings(),
    checkUpdates: () => { openSettings("updates"); void updater.check(true); },
    quit: () => void requestQuit(),
  });

  function openSettings(tab?: string): void { main.reveal(); main.showPage("settings"); main.win.webContents.send("shell:go", { page: "settings", tab }); }

  async function openLink(raw: string): Promise<void> {
    const d = parseDeepLink(raw, { allowHttpLoopback: ctx.flags.allowHttpLoopback });
    main.reveal();
    if (!d) { log.warn("link.rejected"); main.win.webContents.send("shell:toast", { text: "Ссылка не распознана или небезопасна — она не открыта." }); return; }
    const o = orgs.byOrigin(d.origin);
    if (o) {
      const call = orgs.list.find((x) => x.state.inCall);
      if (call && d.route !== "/") {
        const r = await dialog.showMessageBox(main.win, { type: "question", buttons: ["Открыть", "Отмена"], defaultId: 1, cancelId: 1, title: "Peregovorka", message: "Вы участвуете во встрече",
          detail: call === o ? "Переход к другой встрече покинет текущую." : "Текущая встреча продолжится в другой организации." });
        if (r.response !== 0) return;
      }
      focusOrg(o);
      navigate(o, d.route);
      return;
    }
    const can = orgs.canAdd(d.origin);
    main.showPage("add");
    main.win.webContents.send("shell:go", { page: "add", url: d.origin, route: d.route, error: can.ok ? null : can.reason });
  }
  app.on("second-instance", (_e, args) => {
    const link = findDeepLinkArg(args);
    if (link) void openLink(link);
    else main.reveal();
  });
  // схему peregovorka:// регистрируют установщики (EXE — для пользователя или машины, MSI — для машины) и удаляют вместе с программой;
  // сама программа прописывает её только при запуске из исходников
  if (!app.isPackaged && process.platform === "win32" && !app.isDefaultProtocolClient("peregovorka")) app.setAsDefaultProtocolClient("peregovorka", process.execPath, [join(__dirname, "..")]);


  powerMonitor.on("resume", () => { log.info("power.resume"); for (const o of orgs.list) if (o.status === "offline") orgs.reconnect(o.rec.id); });

  // ---------------------------------------------------------------- запуск
  let startupMs: number | null = null;
  const firstShow = !ctx.flags.hidden;
  main.win.once("ready-to-show", () => {
    if (firstShow) main.win.show();
    startupMs = Date.now() - T0;
    log.info("app.ready", { ms: startupMs });
  });
  if (orgs.list.length === 0) main.showPage("add");
  orgs.startAll();
  applyAutostart(ctx.eff().autostart, managedInstall && !!policy.autostart);
  updater.schedule();
  const link = findDeepLinkArg(argv);
  if (link) main.win.webContents.once("did-finish-load", () => void openLink(link));

  const diag = argv.find((a) => a.startsWith("--diagnostics="));
  if (diag) {
    // отчёт — когда окно показано и оболочка отрисована (или через 60 с — тогда в отчёте видно, что не отрисовалась); плюс 5 с на подключение организаций
    void (async () => {
      let sh = await shellHealth();
      for (let i = 0; i < 120 && (startupMs === null || !sh.ok); i++) { await new Promise((res) => setTimeout(res, 500)); sh = await shellHealth(); }
      await new Promise((res) => setTimeout(res, 5000));
      try { writeFileSync(diag.slice(14), JSON.stringify({ ...collect(ctx, orgs, updater.state, null, startupMs), shell: await shellHealth() }, null, 1), "utf8"); } catch (e) { log.error("diag.cli_failed", { error: String(e) }); }
      ctx.quitting = true;
      app.quit();
    })();
  }
  if (ctx.flags.e2e) (globalThis as Record<string, unknown>).__pg = { quit: () => { ctx.quitting = true; app.quit(); }, ctx, orgs, main, picker, indicator, updater, notifier, tray, shellState, openLink, lastDownload: () => lastDownload, childWindows, openDownloaded };
}

function devicesFrom(v: unknown): Devices {
  if (!v || typeof v !== "object") return null;
  const d = v as Record<string, unknown>;
  const n = (x: unknown) => (Number.isInteger(x) && (x as number) >= 0 && (x as number) < 100 ? (x as number) : 0);
  return { audioinput: n(d.audioinput), audiooutput: n(d.audiooutput), videoinput: n(d.videoinput),
    labels: Array.isArray(d.labels) ? d.labels.filter((x) => typeof x === "string").slice(0, 30).map((x: string) => x.slice(0, 120)) : [] };
}

/** Установка из MSI (корпоративная): установщик пишет HKLM\SOFTWARE\Peregovorka\Desktop InstallType=msi — тогда обновления делает администратор. */
function isMsiInstall(): Promise<boolean> {
  if (process.platform !== "win32") return Promise.resolve(false);
  return new Promise((res) => execFile("reg.exe", ["query", "HKLM\\SOFTWARE\\Peregovorka\\Desktop", "/v", "InstallType"], { windowsHide: true, timeout: 5000 },
    (_e, out) => res(/InstallType\s+REG_SZ\s+msi/i.test(String(out || "")))));
}
