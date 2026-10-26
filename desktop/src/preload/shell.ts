// Мост доверенной оболочки (app://shell — файлы из пакета приложения). Перечень команд закрыт; главный процесс принимает их только от окон оболочки.
import { contextBridge, ipcRenderer } from "electron";

const CALLS = ["shell:state", "shell:probe", "shell:add", "shell:activate", "shell:page", "shell:rename", "shell:move", "shell:remove", "shell:signout", "shell:signoutAll",
  "shell:reconnect", "shell:settings", "shell:update:check", "shell:update:download", "shell:update:install", "shell:diag", "shell:diag:export", "shell:logs", "shell:external", "shell:badge", "shell:av:state", "shell:sound:open",
  "shell:picker:data", "shell:picker:choose", "shell:indicator:data", "shell:indicator:act"] as const;
const EVENTS = ["shell:state", "shell:go", "shell:toast", "shell:indicator"] as const;

contextBridge.exposeInMainWorld("pg", Object.freeze({
  call(ch: (typeof CALLS)[number], arg?: unknown): Promise<unknown> {
    if (!(CALLS as readonly string[]).includes(ch)) return Promise.reject(new Error("unknown_call"));
    return ipcRenderer.invoke(ch, arg ?? {});
  },
  on(ev: (typeof EVENTS)[number], cb: (data: unknown) => void): void {
    if (!(EVENTS as readonly string[]).includes(ev) || typeof cb !== "function") return;
    ipcRenderer.on(ev, (_e, d) => cb(d));
  },
}));
