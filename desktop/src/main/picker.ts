// Показ экрана: страница вызывает getDisplayMedia → окно выбора (экраны и окна с миниатюрами, звук компьютера) в доверенной оболочке. Без выбора — отказ.
// Индикатор: пока идёт показ экрана или встреча при свёрнутом окне — маленькое окно поверх всех «Идёт показ экрана · Остановить» (в захват не попадает).
import { BrowserWindow, desktopCapturer, screen, type DisplayMediaRequestHandlerHandlerRequest, type Streams } from "electron";
import type { Ctx } from "./ctx";
import type { Org } from "./orgs";
import { shellPrefs, themeBg } from "./window";

/** Названия экранов от Chromium — по-английски («Entire screen», «Screen 1»): показываем свои. */
function screenName(i: number, all: Electron.DesktopCapturerSource[]): string {
  const screens = all.filter((s) => s.id.startsWith("screen:"));
  return screens.length === 1 ? "Весь экран" : `Экран ${screens.indexOf(all[i]) + 1}`;
}

type Pending = { org: Org; req: DisplayMediaRequestHandlerHandlerRequest; cb: (s: Streams | null) => void; sources: Electron.DesktopCapturerSource[] };

export class ScreenPicker {
  win: BrowserWindow | null = null;
  private pending: Pending | null = null;
  constructor(private ctx: Ctx, private parent: () => BrowserWindow) {}

  async open(org: Org, req: DisplayMediaRequestHandlerHandlerRequest, cb: (s: Streams | null) => void): Promise<void> {
    if (this.pending) { cb(null); return; }                       // один выбор за раз
    let sources: Electron.DesktopCapturerSource[];
    try {
      sources = await desktopCapturer.getSources({ types: ["screen", "window"], thumbnailSize: { width: 320, height: 180 }, fetchWindowIcons: true });
    } catch (e) {
      this.ctx.log.error("share.sources_failed", { error: String(e).slice(0, 160) });
      cb(null);
      return;
    }
    sources = sources.filter((s) => !s.name.startsWith("Peregovorka — индикатор"));
    this.pending = { org, req, cb, sources };
    const w = new BrowserWindow({ parent: this.parent(), modal: true, width: 820, height: 600, minWidth: 640, minHeight: 460, show: false, title: "Показ экрана",
      backgroundColor: themeBg(), autoHideMenuBar: true, minimizable: false, maximizable: false, webPreferences: shellPrefs(this.ctx.flags.devTools) });
    w.setMenu(null);
    this.win = w;
    w.on("closed", () => { this.win = null; this.finish(null, false); });
    w.once("ready-to-show", () => w.show());
    await w.loadURL("app://shell/picker.html");
  }

  /** Данные для окна выбора (только идентификатор, название, вид и миниатюры). */
  data() {
    const p = this.pending;
    if (!p) return null;
    return { org: p.org.rec.name, audio: p.req.audioRequested && process.platform === "win32",
      sources: p.sources.map((s, i, all) => ({ id: s.id, name: s.id.startsWith("screen:") ? screenName(i, all) : s.name, kind: s.id.startsWith("screen:") ? "screen" : "window", thumb: s.thumbnail.isEmpty() ? null : s.thumbnail.toDataURL(),
        icon: s.appIcon && !s.appIcon.isEmpty() ? s.appIcon.toDataURL() : null, display: s.display_id || null })) };
  }

  finish(id: string | null, audio: boolean): void {
    const p = this.pending;
    this.pending = null;
    if (!p) return;
    const src = id ? p.sources.find((s) => s.id === id) : undefined;
    if (!src) { p.cb(null); this.ctx.log.info("share.cancelled", { org: p.org.rec.id }); }
    else {
      const s: Streams = { video: src };
      if (audio && p.req.audioRequested && process.platform === "win32" && src.id.startsWith("screen:")) s.audio = "loopback";
      p.cb(s);
      this.ctx.log.info("share.started", { org: p.org.rec.id, kind: src.id.split(":")[0], audio: !!s.audio });
    }
    if (this.win && !this.win.isDestroyed()) this.win.close();
  }
}

export class Indicator {
  win: BrowserWindow | null = null;
  info: { org: string; sharing: boolean; inCall: boolean; mic: boolean } | null = null;
  constructor(private ctx: Ctx) {}

  update(next: { org: string; sharing: boolean; inCall: boolean; mic: boolean } | null): void {
    this.info = next;
    if (!next) { if (this.win && !this.win.isDestroyed()) this.win.close(); this.win = null; return; }
    if (this.win && !this.win.isDestroyed()) { this.win.webContents.send("shell:indicator", next); return; }
    const wa = screen.getPrimaryDisplay().workArea;
    const width = 420, height = 52;
    const w = new BrowserWindow({ width, height, x: Math.round(wa.x + (wa.width - width) / 2), y: wa.y + 12, frame: false, resizable: false, movable: true, minimizable: false,
      maximizable: false, alwaysOnTop: true, skipTaskbar: true, focusable: true, show: false, title: "Peregovorka — индикатор", backgroundColor: themeBg(true),
      webPreferences: shellPrefs(this.ctx.flags.devTools) });
    w.setAlwaysOnTop(true, "screen-saver");
    w.setContentProtection(true);                 // индикатор не попадает в показ экрана
    this.win = w;
    w.on("closed", () => { if (this.win === w) this.win = null; });
    w.once("ready-to-show", () => w.showInactive());
    void w.loadURL("app://shell/indicator.html");
  }
}
