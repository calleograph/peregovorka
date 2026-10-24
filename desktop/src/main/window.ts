// Главное окно: обычная рамка Windows (свернуть/развернуть/закрыть, привязка Win+стрелки, Alt+Tab работают штатно), слева — боковая панель доверенной
// оболочки (организации, настройки), справа — страница активной организации. Положение окна хранится отдельно от настроек интерфейса.
import { BrowserWindow, nativeTheme, screen, type WebContentsView } from "electron";
import { join } from "node:path";
import { fitBounds, MIN_SIZE, type Rect } from "../common/bounds";
import type { Ctx } from "./ctx";
import type { Orgs } from "./orgs";
import { readJson, writeJson } from "./store";

export const SIDEBAR = 64;

export const shellPrefs = (devTools: boolean): Electron.WebPreferences => ({
  preload: join(__dirname, "preload-shell.js"), contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, spellcheck: false, devTools,
});

export const themeBg = (dark = nativeTheme.shouldUseDarkColors) => (dark ? "#0e1218" : "#f3f5f8");

export class MainWindow {
  win: BrowserWindow;
  page = "org";                       // что показывает оболочка справа: org (страница организации) или свой раздел (settings, add, …)
  private file: string;
  private saveTimer: NodeJS.Timeout | null = null;
  constructor(ctx: Ctx, private orgs: () => Orgs) {
    this.file = join(ctx.userData, "window.json");
    const saved = readJson<Partial<Rect> & { maximized?: boolean }>(this.file, {});
    const b = fitBounds(saved, screen.getAllDisplays().map((d) => d.workArea), screen.getPrimaryDisplay().workArea);
    this.win = new BrowserWindow({
      ...b, minWidth: MIN_SIZE.width, minHeight: MIN_SIZE.height, show: false, title: "Peregovorka", backgroundColor: themeBg(), autoHideMenuBar: true,
      icon: join(__dirname, "assets", "icon.png"), webPreferences: shellPrefs(ctx.flags.devTools),
    });
    this.win.setMenu(null);
    if (saved.maximized) this.win.maximize();
    this.win.on("move", () => this.scheduleSave());
    this.win.on("resize", () => { this.layout(); this.scheduleSave(); });
    this.win.on("maximize", () => { this.layout(); this.scheduleSave(); });
    this.win.on("unmaximize", () => { this.layout(); this.scheduleSave(); });
    screen.on("display-removed", () => this.ensureVisible());
    screen.on("display-metrics-changed", () => this.ensureVisible());
    void this.win.loadURL("app://shell/index.html");
  }

  attach(v: WebContentsView): void { this.win.contentView.addChildView(v); }
  detach(v: WebContentsView): void { try { this.win.contentView.removeChildView(v); } catch { /* уже снято */ } }

  layout(): void {
    if (this.win.isDestroyed()) return;
    const [w, h] = this.win.getContentSize();
    const orgs = this.orgs();
    for (const o of orgs.list) {
      if (!o.view) continue;
      const show = o === orgs.active && this.page === "org" && (o.status === "online" || o.status === "signed-out");
      o.view.setBounds({ x: SIDEBAR, y: 0, width: Math.max(0, w - SIDEBAR), height: h });
      o.view.setVisible(show);
      if (show && this.win.isFocused()) o.view.webContents.focus();
    }
    const a = orgs.active;
    const t = a?.state.title || "Peregovorka";
    this.win.setTitle(orgs.list.length > 1 && a ? `${t} — ${a.rec.name}` : t);
  }

  showPage(page: string): void {
    this.page = /^[a-z-]{1,20}$/.test(page) ? page : "org";
    this.layout();
  }

  reveal(): void {
    if (this.win.isMinimized()) this.win.restore();
    if (!this.win.isVisible()) this.win.show();
    this.win.focus();
  }

  private ensureVisible(): void {
    if (this.win.isDestroyed() || this.win.isMaximized() || this.win.isFullScreen()) return;
    const b = fitBounds(this.win.getBounds(), screen.getAllDisplays().map((d) => d.workArea), screen.getPrimaryDisplay().workArea);
    this.win.setBounds(b);
  }

  private scheduleSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.saveBounds(), 500);
  }

  saveBounds(): void {
    if (this.win.isDestroyed() || this.win.isMinimized() || this.win.isFullScreen()) return;
    const maximized = this.win.isMaximized();
    const b = maximized ? readJson<Partial<Rect>>(this.file, {}) : this.win.getNormalBounds();
    try { writeJson(this.file, { ...b, maximized }); } catch { /* нет места — не критично */ }
  }
}
