/** Словарь обезличивания в CSV: выгрузка «термин,категория,замена» и загрузка её же или выгрузки прежнего обезличивателя документов («значение,метка»). */
export type Term = { term: string; category: string; replacement: string };

const CATS = new Set(["project", "organization", "custom", "host", "person", "location"]);

function rows(text: string): string[][] {
  const out: string[][] = [];
  let row: string[] = [], cell = "", q = false;
  const delim = (text.split("\n")[0].match(/;/g)?.length ?? 0) > (text.split("\n")[0].match(/,/g)?.length ?? 0) ? ";" : ",";
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') q = false; else cell += c;
    } else if (c === '"') q = true;
    else if (c === delim) { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); out.push(row); row = []; cell = "";
    } else cell += c;
  }
  if (cell || row.length) { row.push(cell); out.push(row); }
  return out.map((r) => r.map((x) => x.trim())).filter((r) => r.some(Boolean));
}

/** Термины из CSV и число пропущенных строк (пустой термин, слишком длинное значение). Заголовок первой строки распознаётся и пропускается. */
export function parseTermsCsv(text: string): { terms: Term[]; skipped: number } {
  const all = rows(text.replace(/^﻿/, ""));
  const head = (all[0] ?? []).map((x) => x.toLowerCase());
  const body = head[0] === "термин" || head[0] === "значение" || head[0] === "term" ? all.slice(1) : all;
  const terms: Term[] = [];
  let skipped = 0;
  for (const r of body) {
    const [term = "", second = "", third = ""] = r;
    const isCat = CATS.has(second.toLowerCase());
    // «значение,метка» прежнего обезличивателя: метка становится своей заменой, категория — «свой термин»
    const t: Term = r.length >= 3 || isCat ? { term, category: isCat ? second.toLowerCase() : "custom", replacement: r.length >= 3 ? third : "" }
                                          : { term, category: "custom", replacement: second };
    if (!t.term || t.term.length > 200 || t.replacement.length > 100) { skipped++; continue; }
    terms.push(t);
  }
  return { terms, skipped };
}

const cell = (v: string) => (/[",;\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export function termsToCsv(terms: Term[]): string {
  return ["термин,категория,замена", ...terms.filter((t) => t.term.trim()).map((t) => [t.term, t.category, t.replacement].map(cell).join(","))].join("\n") + "\n";
}

/** Объединение без повторов: термин, который уже есть в словаре (без учёта регистра), не добавляется второй раз. */
export function mergeTerms(current: Term[], added: Term[]): { terms: Term[]; added: number } {
  const seen = new Set(current.map((t) => t.term.trim().toLowerCase()));
  const fresh = added.filter((t) => !seen.has(t.term.trim().toLowerCase()) && seen.add(t.term.trim().toLowerCase()));
  return { terms: [...current, ...fresh], added: fresh.length };
}
