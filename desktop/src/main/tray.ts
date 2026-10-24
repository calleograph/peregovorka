// Значок в области уведомлений: левая кнопка — открыть окно, правая — меню (организации, чаты, предстоящие встречи, текущая встреча, настройки, обновления, выход).
// Есть непрочитанные — значок с отметкой и число во всплывающей подсказке.
import { app, Menu, nativeImage, Tray } from "electron";
import { join } from "node:path";
import type { Autostart } from "../common/policy";
import type { Org } from "./orgs";

export type TrayActions = {
  open(): void; chats(): void; upcoming(): void; call(org: Org): void; switchTo(org: Org): void; settings(page?: string): void; checkUpdates(): void; quit(): void;
};

export class AppTray {
  tray: Tray;
  private icons: { normal: Electron.NativeImage; unread: Electron.NativeImage };
  private last = "";
  constructor(private a: TrayActions) {
    // 16 px для 100 % масштаба и 32 px для 200 % (Windows сам выбирает по масштабу экрана)
    const img = (one: string | null, two: string) => {
      const big = nativeImage.createFromPath(join(__dirname, "assets", two));
      const out = nativeImage.createEmpty();
      out.addRepresentation({ scaleFactor: 1, buffer: (one ? nativeImage.createFromPath(join(__dirname, "assets", one)) : big.resize({ width: 16, height: 16, quality: "best" })).toPNG() });
      out.addRepresentation({ scaleFactor: 2, buffer: big.toPNG() });
      return out;
    };
    this.icons = { normal: img("tray.png", "tray-hi.png"), unread: img(null, "tray-unread-hi.png") };
    this.tray = new Tray(this.icons.normal);
    this.tray.setToolTip("Peregovorka");
    this.tray.on("click", () => a.open());
    this.tray.on("double-click", () => a.open());
  }

  update(orgs: Org[], activeId: string | null): void {
    const unread = orgs.reduce((n, o) => n + (o.state.unread || 0), 0);
    const call = orgs.find((o) => o.state.inCall);
    const key = JSON.stringify([unread, call?.rec.id, call?.state.sharing, activeId, orgs.map((o) => [o.rec.id, o.rec.name, o.status])]);
    if (key === this.last) return;
    this.last = key;
    this.tray.setImage(unread ? this.icons.unread : this.icons.normal);
    this.tray.setToolTip(["Peregovorka", unread ? `непрочитанных: ${unread}` : "", call ? (call.state.sharing ? "идёт показ экрана" : "идёт встреча") : ""].filter(Boolean).join(" — "));
    const t: Electron.MenuItemConstructorOptions[] = [
      { label: "Открыть Peregovorka", click: () => this.a.open() },
      { label: unread ? `Чаты (${unread})` : "Чаты", click: () => this.a.chats(), enabled: orgs.length > 0 },
      { label: "Предстоящие встречи", click: () => this.a.upcoming(), enabled: orgs.length > 0 },
    ];
    if (call) t.push({ label: `Текущая встреча — ${call.rec.name}`, click: () => this.a.call(call) });
    if (orgs.length > 1) {
      t.push({ type: "separator" }, { label: "Организации", submenu: orgs.map((o) => ({ label: `${o.rec.name}${o.state.unread ? ` (${o.state.unread})` : ""}${o.status === "offline" ? " — нет связи" : ""}`,
        type: "radio" as const, checked: o.rec.id === activeId, click: () => this.a.switchTo(o) })) });
    }
    t.push({ type: "separator" }, { label: "Настройки", click: () => this.a.settings() }, { label: "Проверить обновления", click: () => this.a.checkUpdates() },
      { type: "separator" }, { label: "Выйти из приложения", click: () => this.a.quit() });
    this.tray.setContextMenu(Menu.buildFromTemplate(t));
  }
}

/** Автозапуск средствами Windows (раздел реестра «Run» текущего пользователя); «в трей» — с ключом --hidden. Установщик удаляет запись при удалении программы. */
export function applyAutostart(mode: Autostart): void {
  if (!app.isPackaged) return;                         // запуск из исходников не прописывается в автозапуск
  app.setLoginItemSettings({ openAtLogin: mode !== "off", path: process.execPath, args: mode === "tray" ? ["--hidden"] : [] });
}
