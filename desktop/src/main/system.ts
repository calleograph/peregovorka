// Мелкая интеграция с Windows: загрузки через системный диалог «Сохранить как», контекстное меню (копировать/вставить, орфография, ссылки, изображения).
import { app, clipboard, Menu, shell, type DownloadItem, type WebContents } from "electron";
import { extname, join } from "node:path";
import { externalAllowed } from "../common/urls";
import type { Ctx } from "./ctx";

// Исполняемые и сценарные файлы после загрузки не запускаются из уведомления — показываются в папке (решение остаётся за человеком и SmartScreen).
const RISKY = new Set([".exe", ".msi", ".msix", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".scr", ".lnk", ".hta", ".jar", ".reg", ".cpl", ".pif", ".appx", ".url", ".iso", ".img"]);
export const openableAfterDownload = (path: string) => !RISKY.has(extname(path).toLowerCase());

export function handleDownload(ctx: Ctx, item: DownloadItem, done: (name: string, path: string, ok: boolean) => void): void {
  const name = item.getFilename();
  item.setSaveDialogOptions({ title: "Сохранить файл", defaultPath: join(app.getPath("downloads"), name), buttonLabel: "Сохранить" });
  item.once("done", (_e, state) => {
    const path = item.getSavePath();
    ctx.log.info("download.done", { state, ext: extname(path || name).toLowerCase().slice(0, 10) });
    if (state !== "cancelled" && path) done(name, path, state === "completed");
  });
}

export function openDownloaded(path: string): void {
  if (openableAfterDownload(path)) void shell.openPath(path).then((err) => { if (err) shell.showItemInFolder(path); });
  else shell.showItemInFolder(path);
}

export function contextMenu(wc: WebContents, p: Electron.ContextMenuParams): void {
  const t: Electron.MenuItemConstructorOptions[] = [];
  const sep = () => { if (t.length && t[t.length - 1].type !== "separator") t.push({ type: "separator" }); };
  if (p.isEditable) {
    for (const s of p.dictionarySuggestions.slice(0, 5)) t.push({ label: s, click: () => wc.replaceMisspelling(s) });
    if (p.misspelledWord) t.push({ label: "Добавить в словарь", click: () => wc.session.addWordToSpellCheckerDictionary(p.misspelledWord) });
    sep();
    t.push({ label: "Отменить", role: "undo", enabled: p.editFlags.canUndo }, { label: "Повторить", role: "redo", enabled: p.editFlags.canRedo }, { type: "separator" },
      { label: "Вырезать", role: "cut", enabled: p.editFlags.canCut }, { label: "Копировать", role: "copy", enabled: p.editFlags.canCopy },
      { label: "Вставить", role: "paste", enabled: p.editFlags.canPaste }, { label: "Выделить всё", role: "selectAll" });
  } else if (p.selectionText.trim()) {
    t.push({ label: "Копировать", role: "copy" });
  }
  if (p.linkURL && /^(https?|mailto):/i.test(p.linkURL)) {
    sep();
    t.push({ label: "Копировать адрес ссылки", click: () => clipboard.writeText(p.linkURL) });
    if (externalAllowed(p.linkURL)) t.push({ label: "Открыть в браузере", click: () => void shell.openExternal(p.linkURL) });
  }
  if (p.mediaType === "image" && p.srcURL) {
    sep();
    t.push({ label: "Копировать изображение", click: () => wc.copyImageAt(p.x, p.y) });
    if (/^(https?|blob|data):/i.test(p.srcURL)) t.push({ label: "Сохранить изображение как…", click: () => wc.downloadURL(p.srcURL) });
  }
  if (t.length) Menu.buildFromTemplate(t).popup();
}
