// Мост для страницы сервера организации (недоверенное содержимое). Только три действия наружу — уведомление, состояние, готовность — и приём команд
// приложения. Ни файловой системы, ни Node.js, ни произвольного IPC. Главный процесс дополнительно проверяет отправителя и каждое поле.
import { contextBridge, ipcRenderer } from "electron";

const version = (process.argv.find((a) => a.startsWith("--pg-desktop-version=")) || "").split("=")[1] || "";
type Cmd = { type: string; route?: string };
let handler: ((c: Cmd) => void) | null = null;
ipcRenderer.on("desktop:command", (_e, c: Cmd) => {
  if (handler && c && typeof c.type === "string") handler({ type: c.type, route: typeof c.route === "string" ? c.route : undefined });
});

contextBridge.exposeInMainWorld("peregovorkaDesktop", Object.freeze({
  api: 1,
  version,
  platform: process.platform,
  notify(n: { tag: string; title: string; body?: string; route?: string; kind?: string }): void {
    ipcRenderer.send("org:notify", { tag: String(n?.tag ?? ""), title: String(n?.title ?? ""), body: String(n?.body ?? ""), route: n?.route, kind: String(n?.kind ?? "info") });
  },
  setState(s: { unread?: number; inCall?: boolean; sharing?: boolean; mic?: boolean; theme?: string; ws?: string }): void {
    ipcRenderer.send("org:state", { unread: s?.unread, inCall: s?.inCall, sharing: s?.sharing, mic: s?.mic, theme: s?.theme, ws: s?.ws });
  },
  /** Выбор устройств и обработка звука, заданные в настройках приложения (по названию устройства). Страница применяет их как значения по умолчанию; null — нет данных. */
  media: Object.freeze({ get(): Promise<unknown> { return ipcRenderer.invoke("org:media:get"); } }),
  /** Открыть системные настройки звука Windows: mic-classic (окно «Звук», вкладка «Запись»), sound, privacy-mic. Другие значения отклоняются. */
  openSoundSettings(kind: string): Promise<unknown> { return ipcRenderer.invoke("org:sound:open", { kind: String(kind ?? "") }); },
  onCommand(cb: (c: Cmd) => void): void {
    handler = typeof cb === "function" ? cb : null;
    ipcRenderer.send("org:ready");
  },
}));
