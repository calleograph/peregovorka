/** Чистая логика раздела «Резервная копия конфигурации» (проверяется тестами). */

export interface Warn { kind: string; title: string; where: string; value: string | null; note: string }

/** Пароль архива вводят с пробелами и дефисами (как показан): сервер игнорирует их, но проверка длины — по значащим символам. */
export const normalizeArchivePassword = (p: string) => p.replace(/[\s\-–—]/g, "");
export const archivePasswordComplete = (p: string) => normalizeArchivePassword(p).length === 20;

/** Пароль показывается группами по 4 символа — так его проще переписать и продиктовать. */
export const groupPassword = (p: string) => p.replace(/(.{4})(?=.)/g, "$1 ");

export function backupFileName(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `peregovorka-config-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}.pgcfg`;
}

/** Предупреждения о привязке к серверу по видам: так их проще читать, чем одним длинным списком. */
export function groupWarnings(ws: Warn[]): { kind: string; title: string; items: Warn[] }[] {
  const by = new Map<string, { kind: string; title: string; items: Warn[] }>();
  for (const w of ws) {
    const g = by.get(w.kind) ?? { kind: w.kind, title: w.title, items: [] };
    g.items.push(w);
    by.set(w.kind, g);
  }
  return [...by.values()];
}

const STATUS: Record<string, string> = { restored: "работает", needs_attention: "требует внимания", failed: "ошибка проверки" };
export const statusTitle = (s: string) => STATUS[s] ?? s;
export const statusTone = (s: string): "ok" | "warn" | "bad" => (s === "restored" ? "ok" : s === "needs_attention" ? "warn" : "bad");

/** Можно ли нажать «Применить»: предупреждения прочитаны (если они есть) и введён пароль администратора. */
export function canApply(p: { needs_ack: boolean; conflicts: string[] }, ack: boolean, adminPassword: string): boolean {
  if (!adminPassword) return false;
  return !p.needs_ack || ack;
}
