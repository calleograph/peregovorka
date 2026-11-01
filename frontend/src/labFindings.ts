/** Лаборатория обезличивания: найденные люди и спорные кандидаты, сгруппированные по фрагменту, с контекстом первого вхождения. */
import type { PrivacyLabResult } from "./api";

export type Context = { before: string; match: string; after: string };
export type Finding = { original: string; replacement: string; note: string; count: number; context: Context };

export function context(source: string, start: number, end: number, width = 40): Context {
  const line = (s: string) => s.replace(/\s+/g, " ");
  const a = Math.max(0, start - width), b = Math.min(source.length, end + width);
  return { before: (a > 0 ? "…" : "") + line(source.slice(a, start)), match: source.slice(start, end), after: line(source.slice(end, b)) + (b < source.length ? "…" : "") };
}

function group<T extends { start: number; end: number }>(source: string, items: T[], key: (x: T) => string, make: (x: T) => Omit<Finding, "count" | "context">): Finding[] {
  const out = new Map<string, Finding>();
  for (const x of items) {
    const k = key(x);
    const f = out.get(k);
    if (f) f.count += 1;
    else out.set(k, { ...make(x), count: 1, context: context(source, x.start, x.end) });
  }
  return [...out.values()].sort((a, b) => b.count - a.count || a.original.localeCompare(b.original, "ru"));
}

/** Люди, признанные персональными данными (замена, основание), и спорные кандидаты, оставленные текстом (причина). */
export function labFindings(res: PrivacyLabResult): { people: Finding[]; disputed: Finding[] } {
  const people = group(res.source, res.replacements.filter((r) => r.category === "person"), (r) => `${r.original}\u0000${r.evidence ?? ""}`,
    (r) => ({ original: r.original, replacement: r.replacement, note: r.evidence || r.rule }));
  const disputed = group(res.source, res.candidates ?? [], (c) => `${c.original}\u0000${c.reason}`, (c) => ({ original: c.original, replacement: "", note: c.reason }));
  return { people, disputed };
}

/** Исключения настроек + новое (без повторов, без пустых строк). */
export function addException(list: unknown, value: string): string[] {
  const cur = (Array.isArray(list) ? list : []).map(String).filter((s) => s.trim());
  return cur.some((s) => s.trim().toLowerCase() === value.trim().toLowerCase()) ? cur : [...cur, value.trim()];
}
