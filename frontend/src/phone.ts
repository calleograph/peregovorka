/** Телефония и выбор модели: чистые функции интерфейса (проверяются тестами без DOM). */
import type { LlmChoice, LlmEffective, LlmOptions } from "./api";

/** Идентификатор телефонного участника LiveKit: исходящий звонок (p-…) или входящий (sip_…). */
export const isPhoneIdentity = (identity: string): boolean => identity.startsWith("p-") || identity.startsWith("sip_");

/** Как показывать участника на плитке: телефонному абоненту — «Телефон: номер», остальным — имя как есть. */
export function tileName(identity: string, name: string | undefined, attrs?: Record<string, string>): string {
  const n = (name || "").trim();
  if (!isPhoneIdentity(identity)) return n || identity;
  if (n.startsWith("Телефон")) return n;
  const num = attrs?.["sip.phoneNumber"] || n || identity.replace(/^sip_/, "").split("_")[0] || "";
  return `Телефон: ${num}`.trim();
}

/** Номер для набора: пробелы, скобки, точки и дефисы убираются; допустимы цифры и + * #. null — номер некорректен. */
export function normalizeNumber(raw: string): string | null {
  const s = raw.replace(/[\s().\-]/g, "");
  return /^[0-9+*#]{1,32}$/.test(s) ? s : null;
}

/** Номер разрешён, если список префиксов пуст или номер начинается с одного из них (звёздочка в конце шаблона — любое продолжение). */
export function numberAllowed(prefixes: string[], number: string): boolean {
  const list = prefixes.map((p) => p.trim()).filter(Boolean);
  return list.length === 0 || list.some((p) => number.startsWith(p.replace(/\*+$/, "")));
}

export const emptyChoice = (): LlmChoice => ({ mode: "inherit", profile_id: null, local_model: null });

/** Подпись выбора модели для списка/кнопки. */
export function choiceLabel(c: LlmChoice, o: LlmOptions | null | undefined): string {
  if (c.mode === "off") return "Отключена";
  if (c.mode === "local") return o?.local.find((m) => m.id === c.local_model)?.title ?? "Локальная модель";
  if (c.mode === "profile") return o?.profiles.find((p) => p.id === c.profile_id)?.name ?? "Внешний профиль";
  return o ? `Как системная (${o.system.provider === "off" ? "отключена" : o.system.model ?? o.system.name})` : "Как системная";
}

/** Пояснение к текущему состоянию выбора: откуда взята модель и почему возможен запасной вариант. */
export function effectiveText(e: LlmEffective | null | undefined): { tone: "ok" | "warn" | "error"; text: string } | null {
  if (!e) return null;
  if (!e.available) return { tone: "error", text: `Модель недоступна: ${e.reason ?? "причина не указана"}.` };
  const src = e.source === "meeting" ? "для этой встречи" : e.source === "room" ? "для этой комнаты" : "по умолчанию (системная)";
  return e.note ? { tone: "warn", text: `${e.note}` } : { tone: "ok", text: `Будет использована: ${e.name} — ${src}.` };
}
