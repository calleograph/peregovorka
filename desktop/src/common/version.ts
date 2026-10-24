// Версии клиента и совместимость с сервером: клиент и сервер выпускаются отдельно, совместимость — по уровню API (`/api/v1/desktop/info`).

export const CLIENT_API = { min: 1, max: 1 };

export function cmpVersion(a: string, b: string): number {
  const pa = String(a).split(/[.+-]/).slice(0, 3).map((x) => parseInt(x, 10) || 0);
  const pb = String(b).split(/[.+-]/).slice(0, 3).map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < 3; i++) if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) < (pb[i] ?? 0) ? -1 : 1;
  return 0;
}

export type ServerInfo = { product: string; api: number; min_client: string; server_version: string; name: string; icon: string | null; cookie: string; trust_days: number; features: string[] };

export type Compat = { ok: true } | { ok: false; code: "client_too_old" | "server_too_new" | "server_too_old" | "not_peregovorka"; message: string };

export function compat(info: Partial<ServerInfo> | null, clientVersion: string): Compat {
  if (!info || info.product !== "peregovorka" || typeof info.api !== "number") return { ok: false, code: "not_peregovorka", message: "По этому адресу отвечает не сервер Peregovorka." };
  if (info.api > CLIENT_API.max) return { ok: false, code: "server_too_new", message: "Сервер использует более новую версию протокола приложения. Обновите Peregovorka для Windows." };
  if (info.api < CLIENT_API.min) return { ok: false, code: "server_too_old", message: "Версия сервера слишком старая для этого приложения. Обратитесь к администратору сервера." };
  if (info.min_client && cmpVersion(clientVersion, info.min_client) < 0) {
    return { ok: false, code: "client_too_old", message: `Сервер требует Peregovorka для Windows версии ${info.min_client} или новее. Обновите приложение.` };
  }
  return { ok: true };
}
