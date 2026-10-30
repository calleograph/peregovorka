// Организации (подключённые серверы Peregovorka). Каждая — в своём постоянном разделе Electron `persist:org-<id>`: свои cookie, хранилище, кэш, прокси,
// разрешения и WebSocket. Страница сервера — недоверенное содержимое: песочница, без Node.js, мост только из preload-org (уведомления, состояние, команды).
// Неактивные организации остаются загруженными (скрыты): их чаты и уведомления продолжают работать, а встреча не обрывается при переключении.
import { session, shell, WebContentsView, type BrowserWindow, type Session, type WebContents, type DisplayMediaRequestHandlerHandlerRequest, type Streams } from "electron";
import { randomUUID, createHash } from "node:crypto";
import { join } from "node:path";
import { backoffMs, describeNetError, type NetProblem } from "../common/neterr";
import { externalAllowed, safeRoute, sameOrigin, domainAllowed } from "../common/urls";
import type { ServerInfo } from "../common/version";
import type { Ctx } from "./ctx";
import { request } from "./http";
import { readJson, writeJson } from "./store";

export type OrgRecord = { id: string; name: string; origin: string; managed: boolean; icon?: string | null };
export type OrgStatus = "connecting" | "online" | "signed-out" | "offline" | "error";
export type PageState = { unread: number; inCall: boolean; sharing: boolean; mic: boolean; theme: "light" | "dark" | null; ws: string | null; title: string | null };

export interface Host {
  attach(view: WebContentsView): void;
  detach(view: WebContentsView): void;
  layout(): void;
  pickScreen(org: Org, req: DisplayMediaRequestHandlerHandlerRequest, cb: (s: Streams | null) => void): void;
  notify(org: Org, n: { tag: string; title: string; body?: string; route?: string; kind: string }): void;
  confirmReloadInCall(): boolean;
  download(org: Org, item: Electron.DownloadItem, wc: WebContents): void;
  contextMenu(wc: WebContents, p: Electron.ContextMenuParams): void;
  childWindowOptions(org: Org): Electron.BrowserWindowConstructorOptions;
  windowCreated(org: Org, w: BrowserWindow): void;
}

const EMPTY: PageState = { unread: 0, inCall: false, sharing: false, mic: false, theme: null, ws: null, title: null };

export class Org {
  view: WebContentsView | null = null;
  status: OrgStatus = "connecting";
  problem: NetProblem | null = null;
  info: ServerInfo | null = null;
  user: string | null = null;
  attempt = 0;
  retryAt = 0;
  private retryTimer: NodeJS.Timeout | null = null;
  private crashes: number[] = [];
  readonly pages = new Map<number, PageState>();        // по webContents: главное представление и окна, открытые из страницы
  readonly ses: Session;
  constructor(public rec: OrgRecord) {
    this.ses = session.fromPartition(`persist:org-${rec.id}`);
  }
  get state(): PageState {
    const main = this.view ? this.pages.get(this.view.webContents.id) : undefined;
    const all = [...this.pages.values()];
    return { ...(main ?? EMPTY), inCall: all.some((p) => p.inCall), sharing: all.some((p) => p.sharing), mic: all.some((p) => p.mic && p.inCall) };
  }
  clearRetry(): void { if (this.retryTimer) clearTimeout(this.retryTimer); this.retryTimer = null; this.retryAt = 0; }
  scheduleRetry(fn: () => void): void {
    this.clearRetry();
    const ms = backoffMs(this.attempt++);
    this.retryAt = Date.now() + ms;
    this.retryTimer = setTimeout(fn, ms);
  }
  noteCrash(): boolean {
    const now = Date.now();
    this.crashes = [...this.crashes.filter((t) => now - t < 5 * 60_000), now];
    return this.crashes.length <= 3;
  }
}

export const managedId = (origin: string) => "m-" + createHash("sha256").update(origin).digest("hex").slice(0, 16);

export class Orgs {
  list: Org[] = [];
  activeId: string | null = null;
  private file: string;
  private watched = new WeakSet<Session>();
  constructor(private ctx: Ctx, private host: Host) {
    this.file = join(ctx.userData, "orgs.json");
  }

  get active(): Org | null { return this.list.find((o) => o.rec.id === this.activeId) ?? null; }
  byId(id: unknown): Org | null { return this.list.find((o) => o.rec.id === id) ?? null; }
  byOrigin(origin: string): Org | null { return this.list.find((o) => o.rec.origin === origin) ?? null; }
  byContents(wc: WebContents): Org | null {
    return this.list.find((o) => o.pages.has(wc.id) || o.view?.webContents.id === wc.id) ?? null;
  }

  canAdd(origin?: string): { ok: boolean; reason?: string } {
    const p = this.ctx.policy;
    if (p.allowAddServers === false) return { ok: false, reason: "Добавление серверов запрещено политикой организации." };
    if (origin && !domainAllowed(new URL(origin).hostname, p.allowedDomains)) return { ok: false, reason: "Этот домен не разрешён политикой организации." };
    return { ok: true };
  }

  load(): void {
    const saved = readJson<{ items?: OrgRecord[] }>(this.file, {}).items ?? [];
    const recs: OrgRecord[] = [];
    for (const s of this.ctx.policy.servers) {
      const old = saved.find((x) => x.origin === s.origin);
      recs.push({ id: managedId(s.origin), name: old?.name || s.name, origin: s.origin, managed: true, icon: old?.icon ?? null });
    }
    for (const r of saved) {
      if (r.managed || recs.some((x) => x.origin === r.origin && x.id === r.id)) continue;
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(r.id) || typeof r.origin !== "string") continue;
      if (!domainAllowed(new URL(r.origin).hostname, this.ctx.policy.allowedDomains)) continue;     // политика сузила список доменов — подключение скрыто (данные остаются)
      recs.push({ id: r.id, name: String(r.name || "").slice(0, 60) || new URL(r.origin).hostname, origin: r.origin, managed: false, icon: r.icon ?? null });
    }
    this.list = recs.map((r) => new Org(r));
    const want = this.ctx.settings.get().activeOrg;
    this.activeId = this.byId(want)?.rec.id ?? this.list[0]?.rec.id ?? null;
  }

  save(): void {
    writeJson(this.file, { items: this.list.map((o) => o.rec) });
  }

  // ---------------------------------------------------------------- жизненный цикл
  startAll(): void {
    for (const o of this.list) void this.start(o);
  }

  add(name: string, origin: string, info: ServerInfo | null): Org {
    const exists = this.byOrigin(origin);
    if (exists) return exists;
    const o = new Org({ id: randomUUID().replace(/-/g, "").slice(0, 20), name: (name || "").trim().slice(0, 60) || info?.name || new URL(origin).hostname, origin, managed: false, icon: null });
    o.info = info;
    this.list.push(o);
    this.save();
    void this.start(o);
    this.activate(o.rec.id);
    return o;
  }

  async remove(id: string): Promise<boolean> {
    const o = this.byId(id);
    if (!o || o.rec.managed) return false;
    o.clearRetry();
    await this.forgetTrust(o);
    this.destroyView(o);
    await o.ses.clearStorageData().catch(() => undefined);
    await o.ses.clearCache().catch(() => undefined);
    await o.ses.clearAuthCache().catch(() => undefined);
    this.list = this.list.filter((x) => x !== o);
    if (this.activeId === id) this.activeId = this.list[0]?.rec.id ?? null;
    this.save();
    this.ctx.settings.patch({ activeOrg: this.activeId });
    this.host.layout();
    this.ctx.emit();
    return true;
  }

  rename(id: string, name: string): void {
    const o = this.byId(id);
    if (!o) return;
    o.rec.name = String(name || "").trim().slice(0, 60) || o.info?.name || new URL(o.rec.origin).hostname;
    this.save();
    this.ctx.emit();
  }

  move(id: string, dir: -1 | 1): void {
    const i = this.list.findIndex((o) => o.rec.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= this.list.length) return;
    [this.list[i], this.list[j]] = [this.list[j], this.list[i]];
    this.save();
    this.ctx.emit();
  }

  activate(id: string): void {
    if (!this.byId(id)) return;
    this.activeId = id;
    this.ctx.settings.patch({ activeOrg: id });
    this.host.layout();
    this.ctx.emit();
  }

  /** Подключение: действующий сеанс → страница; иначе сохранённый вход (токен устройства) → новый сеанс; иначе страница входа этой организации. */
  async start(o: Org): Promise<void> {
    o.clearRetry();
    o.status = "connecting";
    this.ctx.emit();
    this.setupSession(o);
    try {
      const info = await request(o.ses, "GET", `${o.rec.origin}/api/v1/desktop/info`);
      if (info.status === 200) o.info = info.json as ServerInfo;
      if (o.info?.icon && !o.rec.icon) void this.fetchIcon(o);
      const signed = await this.ensureSession(o);
      o.status = signed ? "online" : "signed-out";
      o.problem = null;
      o.attempt = 0;
    } catch (e) {
      o.problem = describeNetError(e);
      o.status = "offline";
      this.ctx.log.warn("org.offline", { org: o.rec.id, code: o.problem.code });
      o.scheduleRetry(() => void this.start(o));
      this.ctx.emit();
      return;
    }
    this.ensureView(o);
    void o.view!.webContents.loadURL(`${o.rec.origin}/`).catch(() => undefined);
    this.ctx.emit();
  }

  reconnect(id: string): void {
    const o = this.byId(id);
    if (!o) return;
    o.attempt = 0;
    if (o.view && o.status !== "offline") o.view.webContents.reload();
    else void this.start(o);
  }

  private async ensureSession(o: Org): Promise<boolean> {
    const me = await request(o.ses, "GET", `${o.rec.origin}/api/v1/auth/me`);
    if (me.status === 200) {
      o.user = String((me.json as { user?: { display_name?: string } })?.user?.display_name ?? "") || null;
      if (!this.ctx.trust.get(o.rec.id)) void this.enroll(o);
      return true;
    }
    const token = this.ctx.trust.get(o.rec.id);
    if (!token) return false;
    const r = await request(o.ses, "POST", `${o.rec.origin}/api/v1/desktop/resume`, { token });
    if (r.status === 200) {
      const j = r.json as { token: string | null; user?: { display_name?: string } };
      if (j.token) this.ctx.trust.set(o.rec.id, j.token); else this.ctx.trust.delete(o.rec.id);
      o.user = j.user?.display_name ?? null;
      this.ctx.log.info("org.resumed", { org: o.rec.id });
      return true;
    }
    if (r.status === 401 || r.status === 403) {
      this.ctx.trust.delete(o.rec.id);                  // отозван, истёк, учётная запись отключена или пароль сменён — только вход этой организации
      this.ctx.log.info("org.trust_rejected", { org: o.rec.id, status: r.status, code: (r.json as { detail?: { code?: string } })?.detail?.code });
      return false;
    }
    if (r.status === 503 || r.status >= 500) throw new Error("net::ERR_CONNECTION_REFUSED");          // каталог или сервер временно недоступен — повторим, токен сохраняется
    return false;                                                                                     // 409 — нет места в лимите: страница входа предложит завершить сеанс
  }

  /** После входа паролем на странице: попросить у сервера токен устройства (сервер выдаёт его только приложению и только полноценному сеансу). */
  private enrolling = new Set<string>();
  async enroll(o: Org): Promise<void> {
    if (!this.ctx.trust.available() || !o.info || o.info.trust_days <= 0 || this.enrolling.has(o.rec.id)) return;
    this.enrolling.add(o.rec.id);
    try {
      const me = await request(o.ses, "GET", `${o.rec.origin}/api/v1/auth/me`);
      if (me.status !== 200) { this.ctx.log.info("org.trust_skipped", { org: o.rec.id, me: me.status }); return; }
      const j = me.json as { csrf_token?: string; must_change_password?: boolean; must_enroll_totp?: boolean; user?: { display_name?: string } };
      o.user = j.user?.display_name ?? o.user;
      if (o.status === "signed-out") { o.status = "online"; this.ctx.emit(); }
      if (j.must_change_password || j.must_enroll_totp || !j.csrf_token) return;
      const r = await request(o.ses, "POST", `${o.rec.origin}/api/v1/desktop/trust`, undefined, { "X-CSRF-Token": j.csrf_token });
      const t = (r.json as { token?: string })?.token;
      if (r.status === 200 && t && this.ctx.trust.set(o.rec.id, t)) this.ctx.log.info("org.trusted", { org: o.rec.id });
      else this.ctx.log.warn("org.trust_refused", { org: o.rec.id, status: r.status, code: (r.json as { detail?: { code?: string } | string })?.detail });
    } catch (e) {
      this.ctx.log.warn("org.trust_failed", { org: o.rec.id, error: String((e as Error)?.message ?? e).slice(0, 120) });
    } finally {
      this.enrolling.delete(o.rec.id);
      this.ctx.emit();
    }
  }

  private async forgetTrust(o: Org): Promise<void> {
    const t = this.ctx.trust.get(o.rec.id);
    this.ctx.trust.delete(o.rec.id);
    if (t) await request(o.ses, "POST", `${o.rec.origin}/api/v1/desktop/forget`, { token: t }, {}, 5000).catch(() => undefined);
  }

  async signOut(id: string): Promise<void> {
    const o = this.byId(id);
    if (!o) return;
    await this.forgetTrust(o);
    try {
      const me = await request(o.ses, "GET", `${o.rec.origin}/api/v1/auth/me`, undefined, {}, 5000);
      const csrf = (me.json as { csrf_token?: string })?.csrf_token;
      if (me.status === 200 && csrf) await request(o.ses, "POST", `${o.rec.origin}/api/v1/auth/logout`, undefined, { "X-CSRF-Token": csrf }, 5000);
    } catch { /* сервер недоступен: локально выходим всё равно */ }
    await o.ses.clearStorageData({ storages: ["cookies"] }).catch(() => undefined);
    o.user = null;
    o.status = "signed-out";
    o.view?.webContents.loadURL(`${o.rec.origin}/`).catch(() => undefined);
    this.ctx.emit();
  }

  async signOutAll(): Promise<void> {
    await Promise.all(this.list.map((o) => this.signOut(o.rec.id)));
  }

  private async fetchIcon(o: Org): Promise<void> {
    try {
      const r = await o.ses.fetch(`${o.rec.origin}${o.info!.icon}`);
      const type = r.headers.get("content-type") || "";
      const buf = Buffer.from(await r.arrayBuffer());
      if (r.ok && /^image\/(png|svg\+xml|x-icon|vnd\.microsoft\.icon|webp|jpeg)$/.test(type.split(";")[0]) && buf.length < 200_000) {
        o.rec.icon = `data:${type.split(";")[0]};base64,${buf.toString("base64")}`;
        this.save();
        this.ctx.emit();
      }
    } catch { /* без значка — буквы названия */ }
  }

  // ---------------------------------------------------------------- представление страницы
  private ensureView(o: Org): void {
    if (o.view) return;
    const v = new WebContentsView({ webPreferences: this.webPreferences(o) });
    v.setBackgroundColor("#00000000");
    o.view = v;
    o.pages.set(v.webContents.id, { ...EMPTY });
    this.setupContents(o, v.webContents);
    this.host.attach(v);
    this.host.layout();
  }

  private destroyView(o: Org): void {
    if (!o.view) return;
    const v = o.view;
    o.view = null;
    o.pages.clear();
    this.host.detach(v);
    v.webContents.close();
  }

  webPreferences(o: Org): Electron.WebPreferences {
    return {
      session: o.ses, preload: join(__dirname, "preload-org.js"), contextIsolation: true, sandbox: true, nodeIntegration: false, nodeIntegrationInSubFrames: false,
      webSecurity: true, allowRunningInsecureContent: false, experimentalFeatures: false, webviewTag: false, navigateOnDragDrop: false, spellcheck: true,
      backgroundThrottling: false,         // неактивная организация продолжает принимать сообщения и уведомления без задержек
      devTools: this.ctx.flags.devTools, additionalArguments: [`--pg-desktop-version=${this.ctx.version}`],
    };
  }

  setupContents(o: Org, wc: WebContents): void {
    o.pages.set(wc.id, o.pages.get(wc.id) ?? { ...EMPTY });
    wc.on("destroyed", () => { o.pages.delete(wc.id); this.ctx.emit(); });
    wc.setWindowOpenHandler(({ url }) => {
      if (sameOrigin(url, o.rec.origin)) return { action: "allow", overrideBrowserWindowOptions: this.host.childWindowOptions(o) };
      if (externalAllowed(url)) void shell.openExternal(url);
      else this.ctx.log.warn("nav.blocked_window", { org: o.rec.id, scheme: url.split(":")[0] });
      return { action: "deny" };
    });
    wc.on("did-create-window", (w) => { this.setupContents(o, w.webContents); this.host.windowCreated(o, w); });
    const guard = (e: Electron.Event, url: string) => {
      if (sameOrigin(url, o.rec.origin)) return;
      e.preventDefault();
      if (externalAllowed(url)) void shell.openExternal(url);
      this.ctx.log.info("nav.external", { org: o.rec.id, scheme: url.split(":")[0] });
    };
    wc.on("will-navigate", guard);
    wc.on("will-redirect", (e) => { if (e.isMainFrame) guard(e, e.url); });
    wc.on("will-attach-webview", (e) => e.preventDefault());
    wc.on("did-fail-load", (_e, code, desc, url, isMain) => {
      if (!isMain || code === -3) return;                       // -3: отменено (переход внутри страницы)
      o.problem = describeNetError(`net::${desc}`);
      o.status = "offline";
      this.ctx.log.warn("org.load_failed", { org: o.rec.id, code: desc, path: (() => { try { return new URL(url).pathname; } catch { return ""; } })() });
      if (wc === o.view?.webContents) o.scheduleRetry(() => void this.start(o));
      this.host.layout();
      this.ctx.emit();
    });
    wc.on("did-finish-load", () => {
      if (wc !== o.view?.webContents) return;
      if (o.status === "offline" || o.status === "connecting") o.status = "online";
      o.problem = null;
      o.attempt = 0;
      this.host.layout();
      this.ctx.emit();
    });
    wc.on("render-process-gone", (_e, d) => {
      this.ctx.log.error("org.renderer_gone", { org: o.rec.id, reason: d.reason, code: d.exitCode });
      if (d.reason !== "clean-exit" && wc === o.view?.webContents && o.noteCrash()) setTimeout(() => { if (!wc.isDestroyed()) wc.reload(); }, 1000);
    });
    wc.on("page-title-updated", (_e, title) => { const p = o.pages.get(wc.id); if (p) p.title = title.slice(0, 200); this.ctx.emit(); });
    wc.on("context-menu", (_e, p) => this.host.contextMenu(wc, p));
    wc.on("before-input-event", (e, i) => {
      if (i.type !== "keyDown") return;
      const ctrl = i.control || i.meta;
      if ((i.key === "F5" || (ctrl && i.key.toLowerCase() === "r")) && !i.alt) {
        e.preventDefault();
        if (!o.state.inCall || this.host.confirmReloadInCall()) wc.reload();
      } else if (ctrl && (i.key === "=" || i.key === "+")) { e.preventDefault(); wc.setZoomLevel(Math.min(3, wc.getZoomLevel() + 0.5)); }
      else if (ctrl && i.key === "-") { e.preventDefault(); wc.setZoomLevel(Math.max(-3, wc.getZoomLevel() - 0.5)); }
      else if (ctrl && i.key === "0") { e.preventDefault(); wc.setZoomLevel(0); }
      else if ((i.key === "F12" || (ctrl && i.shift && i.key.toLowerCase() === "i")) && this.ctx.flags.devTools) { e.preventDefault(); wc.toggleDevTools(); }
    });
  }

  /** Раздел организации: разрешения только её собственному адресу, выбор экрана — через окно выбора, загрузки — через системный диалог. Один раз на сессию. */
  setupSession(o: Org): void {
    const ses = o.ses;
    if (this.watched.has(ses)) return;
    this.watched.add(ses);
    const own = (origin: string | undefined | null) => !!origin && sameOrigin(origin, o.rec.origin);
    const ALLOW = new Set(["media", "fullscreen", "clipboard-sanitized-write", "speaker-selection", "display-capture"]);
    ses.setPermissionRequestHandler((wc, perm, cb, d) => {
      const ok = ALLOW.has(perm) && own(d.requestingUrl) && this.byContents(wc) === o;
      if (!ok) this.ctx.log.info("perm.denied", { org: o.rec.id, perm });
      cb(ok);
    });
    ses.setPermissionCheckHandler((wc, perm, origin) => ALLOW.has(perm) && own(origin) && (!wc || this.byContents(wc) === o));
    ses.setDevicePermissionHandler(() => false);
    ses.on("select-hid-device", (e, _d, cb) => { e.preventDefault(); cb(""); });
    ses.on("select-serial-port", (e, _p, _wc, cb) => { e.preventDefault(); cb(""); });
    ses.on("select-usb-device", (e, _d, cb) => { e.preventDefault(); cb(); });
    ses.setDisplayMediaRequestHandler((req, cb) => {
      if (!own(req.securityOrigin) || !req.frame) { cb(null); return; }
      this.host.pickScreen(o, req, cb);
    });
    // Электрон держит ОДИН обработчик каждого события webRequest на сеанс (следующий заменяет прежний), поэтому всё, что нужно в журнале, разбирается в одном месте.
    // 1) картинки страницы (значки комнат, аватарки) идут на сервер этой организации; сбой попадает в журнал (только путь, без параметров), а страница сама показывает запасной значок;
    // 2) вложения чата (загрузка и открытие идут через fetch, а не как картинки): код ответа или сетевая ошибка — чтобы при жалобе «изображение у других не открывается» было видно,
    //    дошёл ли файл до сервера и что ответил сервер. Тело и заголовки не пишутся.
    const api = { urls: [`${o.rec.origin}/api/*`] };
    const isFile = (u: string) => /\/api\/v1\/(meetings\/[^/]+\/chat\/attachments|chats\/[^/]+\/files)/.test(u);
    const logReq = (d: { method: string; url: string; resourceType?: string }, result: number | string) => {
      let path = d.url.split("?")[0];
      try { path = new URL(d.url).pathname; } catch { /* оставляем как есть */ }
      path = path.slice(0, 120);
      const bad = typeof result === "string" || result >= 400;
      if (d.resourceType === "image") { if (bad) this.ctx.log.warn("page.image_failed", { org: o.rec.id, status: result, path }); return; }
      if (!isFile(d.url)) return;
      const upload = d.method === "POST";
      if (!upload && !bad) return;                                                    // успешные открытия не пишем
      this.ctx.log[bad ? "warn" : "info"]("page.attachment", { org: o.rec.id, op: upload ? "upload" : d.method === "DELETE" ? "discard" : "open", result, path });
    };
    ses.webRequest.onCompleted(api, (d) => logReq(d, d.statusCode));
    ses.webRequest.onErrorOccurred(api, (d) => { if (d.error !== "net::ERR_ABORTED") logReq(d, d.error); });
    ses.on("will-download", (_e, item, wc) => this.host.download(o, item, wc));
    const ua = ses.getUserAgent().replace(/\s(Electron|peregovorka-desktop|Peregovorka)\/[\d.]+/gi, "");
    ses.setUserAgent(`${ua} PeregovorkaDesktop/${this.ctx.version}`);
    try { ses.setSpellCheckerLanguages(["ru", "en-US"]); } catch { /* Windows сам выбирает языки проверки */ }
    void this.applyProxy(ses);
    // вход паролем на странице выставляет cookie сеанса → попросить токен устройства
    ses.cookies.on("changed", (_e, c, _cause, removed) => {
      if (removed || !o.info || c.name !== o.info.cookie) return;
      setTimeout(() => void this.enroll(o), 1500);
    });
  }

  async applyProxy(ses: Session): Promise<void> {
    const p = this.ctx.eff().proxy;
    const cfg: Electron.ProxyConfig = p.mode === "direct" ? { mode: "direct" } : p.mode === "manual" && p.rules ? { mode: "fixed_servers", proxyRules: p.rules, proxyBypassRules: p.bypass || "<local>" }
      : p.mode === "pac" && p.pac ? { mode: "pac_script", pacScript: p.pac } : { mode: "system" };
    await ses.setProxy(cfg).catch((e) => this.ctx.log.warn("proxy.failed", { error: String(e).slice(0, 120) }));
    await ses.closeAllConnections().catch(() => undefined);
  }

  async applyProxyAll(): Promise<void> {
    await Promise.all([...this.list.map((o) => this.applyProxy(o.ses)), this.applyProxy(session.fromPartition("probe"))]);
  }

  /** Команда странице организации (переход, открыть чаты, покинуть встречу, остановить показ экрана). Путь проверяется ещё раз. */
  command(o: Org, cmd: { type: string; route?: string }): void {
    const wc = o.view?.webContents;
    if (!wc || wc.isDestroyed()) return;
    if (cmd.type === "navigate") {
      const r = safeRoute(cmd.route);
      if (!r) return;
      cmd = { type: "navigate", route: r };
    }
    wc.send("desktop:command", cmd);
  }
}
