// «Диагностика»: технический отчёт для поддержки и администратора. В отчёт НЕ попадают пароли, токены, cookie, содержимое сообщений, имена собеседников;
// журнал — последние строки после маскирования. Тот же отчёт без интерфейса: `Peregovorka.exe --diagnostics=<файл>` (см. docs/DESKTOP.md).
import { app, Notification, screen, systemPreferences } from "electron";
import os from "node:os";
import { redact } from "../common/redact";
import type { Ctx } from "./ctx";
import type { Orgs } from "./orgs";
import { lockedFields } from "./settings";
import type { UpdateState } from "./updater";

export type Devices = { audioinput: number; audiooutput: number; videoinput: number; labels: string[] } | null;

export function collect(ctx: Ctx, orgs: Orgs, update: UpdateState, devices: Devices, startupMs: number | null) {
  const perm = (k: "camera" | "microphone" | "screen") => { try { return systemPreferences.getMediaAccessStatus(k); } catch { return "unknown"; } };
  const metrics = app.getAppMetrics().map((m) => ({ type: m.type, mb: Math.round((m.memory.workingSetSize || 0) / 1024), cpu: Math.round(m.cpu.percentCPUUsage * 10) / 10 }));
  const s = ctx.eff();
  return {
    generated_at: new Date().toISOString(),
    client: { version: ctx.version, build: ctx.buildKind, electron: process.versions.electron, chrome: process.versions.chrome, node: process.versions.node,
      os: `${os.type()} ${os.release()}`, arch: process.arch, locale: app.getLocale(), packaged: app.isPackaged, install: ctx.install, startup_ms: startupMs,
      displays: screen.getAllDisplays().map((d) => ({ w: d.size.width, h: d.size.height, scale: d.scaleFactor, primary: d.id === screen.getPrimaryDisplay().id })) },
    settings: { closeAction: s.closeAction, autostart: s.autostart, updateMode: s.updateMode, notifications: { enabled: s.notifications.enabled, sound: s.notifications.sound,
      hideContent: s.notifications.hideContent, dnd: s.notifications.dndUntil === -1 || s.notifications.dndUntil > Date.now() }, proxy: s.proxy.mode },
    policy: { locked: lockedFields(ctx.policy), servers: ctx.policy.servers.length, allowAddServers: ctx.policy.allowAddServers ?? true, allowedDomains: ctx.policy.allowedDomains ?? [] },
    trust_storage: ctx.trust.available() ? "os" : "unavailable",
    notifications_supported: Notification.isSupported(),
    organizations: orgs.list.map((o) => ({ host: new URL(o.rec.origin).host, managed: o.rec.managed, active: o.rec.id === orgs.activeId, status: o.status,
      problem: o.problem?.code ?? null, server_version: o.info?.server_version ?? null, api: o.info?.api ?? null, trust_days: o.info?.trust_days ?? null,
      saved_sign_in: !!ctx.trust.get(o.rec.id), websocket: o.state.ws, in_call: o.state.inCall, sharing: o.state.sharing, unread: o.state.unread })),
    permissions: { camera: perm("camera"), microphone: perm("microphone"), screen: perm("screen") },
    devices,
    gpu: app.getGPUFeatureStatus(),
    processes: metrics,
    update: { status: update.status, version: update.version ?? null, error: update.error ?? null },
    log_tail: redact(ctx.log.tail(300)),
  };
}
