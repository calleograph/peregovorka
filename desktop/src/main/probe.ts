// Проверка сервера в мастере подключения: доступность, HTTPS (решение о доверии принимает Chromium по хранилищу сертификатов Windows), совместимость API,
// возможности для приложения и вход. Результат — список шагов с понятными сообщениями, без трассировок.
import { session } from "electron";
import tls from "node:tls";
import { describeNetError } from "../common/neterr";
import { compat, type ServerInfo } from "../common/version";
import { request } from "./http";

export type Step = { id: "reach" | "tls" | "api" | "features" | "auth"; title: string; state: "ok" | "fail" | "warn" | "skip"; text: string };
export type ProbeResult = { ok: boolean; origin: string; info: ServerInfo | null; steps: Step[]; problem?: { title: string; hint: string; code: string; cert?: CertInfo } };
export type CertInfo = { subject: string; issuer: string; validFrom: string; validTo: string; sha256: string };

const TITLES: Record<Step["id"], string> = { reach: "Доступность сервера", tls: "Защищённое соединение (HTTPS)", api: "Совместимость версии", features: "Возможности для приложения", auth: "Вход" };

/** Сведения о сертификате ТОЛЬКО для показа человеку, когда Chromium уже отклонил соединение (кто выдал, срок). Данные по этому соединению не передаются,
 *  решение о доверии этой функцией не принимается. Через прокси не работает — тогда сведений просто нет. */
export function certDetails(host: string, port: number): Promise<CertInfo | null> {
  return new Promise((resolve) => {
    const s = tls.connect({ host, port, servername: host, rejectUnauthorized: false, timeout: 5000 }, () => {
      const c = s.getPeerCertificate();
      s.destroy();
      if (!c || !c.subject) return resolve(null);
      const name = (x: Record<string, string | string[]> | undefined) => [x?.CN, x?.O].flat().filter(Boolean).join(", ");
      resolve({ subject: name(c.subject as never), issuer: name(c.issuer as never), validFrom: c.valid_from, validTo: c.valid_to, sha256: c.fingerprint256 });
    });
    s.on("error", () => resolve(null));
    s.on("timeout", () => { s.destroy(); resolve(null); });
  });
}

export async function probe(origin: string, clientVersion: string, ses = session.fromPartition("probe")): Promise<ProbeResult> {
  const steps: Step[] = [];
  const add = (id: Step["id"], state: Step["state"], text: string) => steps.push({ id, title: TITLES[id], state, text });
  const rest = (from: Step["id"][]) => from.forEach((id) => add(id, "skip", "Не проверялось"));
  let r;
  try {
    r = await request(ses, "GET", `${origin}/api/v1/desktop/info`);
  } catch (e) {
    const p = describeNetError(e);
    if (p.kind === "tls") {
      add("reach", "ok", "Сервер отвечает");
      add("tls", "fail", p.title);
      rest(["api", "features", "auth"]);
      const u = new URL(origin);
      const cert = await certDetails(u.hostname, Number(u.port) || 443);
      return { ok: false, origin, info: null, steps, problem: { ...p, cert: cert ?? undefined } };
    }
    add("reach", "fail", p.title);
    rest(["tls", "api", "features", "auth"]);
    return { ok: false, origin, info: null, steps, problem: p };
  }
  add("reach", "ok", "Сервер отвечает");
  add("tls", "ok", origin.startsWith("https:") ? "Сертификат действителен и доверен" : "Тестовый стенд без HTTPS (только для разработки)");
  if (r.status === 404) {
    let old: string | null = null;
    try {
      const v = await request(ses, "GET", `${origin}/api/v1/version`);
      old = v.status === 200 && typeof (v.json as { version?: unknown })?.version === "string" ? (v.json as { version: string }).version : null;
    } catch { /* нет */ }
    const text = old ? `Сервер Peregovorka ${old} не поддерживает приложение: нужна версия сервера 0.37.0 или новее` : "По этому адресу отвечает не сервер Peregovorka";
    add("api", "fail", text);
    rest(["features", "auth"]);
    return { ok: false, origin, info: null, steps, problem: { code: old ? "server_too_old" : "not_peregovorka", title: old ? "Не поддерживается версия сервера" : "Это не сервер Peregovorka",
      hint: old ? "Попросите администратора обновить сервер Peregovorka." : "Проверьте адрес: укажите адрес, который открываете в браузере для входа в Peregovorka." } };
  }
  if (r.status !== 200) {
    add("api", "fail", `Сервер ответил ошибкой (${r.status})`);
    rest(["features", "auth"]);
    return { ok: false, origin, info: null, steps, problem: { code: `http_${r.status}`, title: "Сервер временно не готов", hint: "Повторите попытку через минуту. Если ошибка не проходит — сообщите администратору." } };
  }
  const info = r.json as ServerInfo;
  const c = compat(info, clientVersion);
  if (!c.ok) {
    add("api", "fail", c.message);
    rest(["features", "auth"]);
    return { ok: false, origin, info: null, steps, problem: { code: c.code, title: "Не поддерживается требуемая версия API", hint: c.message } };
  }
  add("api", "ok", `Сервер Peregovorka ${info.server_version}`);
  add("features", info.trust_days > 0 ? "ok" : "warn", info.trust_days > 0 ? `Вход сохраняется на ${info.trust_days} дн. (пароль не хранится)` : "Сохранение входа отключено администратором: после окончания сеанса вход нужно повторять");
  try {
    const me = await request(ses, "GET", `${origin}/api/v1/auth/me`);
    add("auth", me.status === 401 || me.status === 200 ? "ok" : "warn", me.status === 401 || me.status === 200 ? "Вход доступен" : `Сервер ответил ${me.status}`);
  } catch {
    add("auth", "warn", "Не удалось проверить");
  }
  return { ok: true, origin, info, steps };
}
