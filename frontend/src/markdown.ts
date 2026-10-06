/**
 * Безопасный разбор Markdown в дерево (AST) для протоколов, которые пишет LLM или правит человек.
 * HTML НЕ интерпретируется вообще: любые теги остаются обычным текстом, а отрисовка идёт только через React-элементы
 * (без dangerouslySetInnerHTML), поэтому внедрить скрипт, обработчик события или стиль через текст протокола нельзя.
 * Ссылки пропускаются только со схемами http, https и mailto; javascript:, data:, vbscript: и прочее отбрасываются
 * (остаётся подпись без ссылки).
 *
 * Поддержка: заголовки #…######, жирный (две звёздочки или два подчёркивания), курсив (одна звёздочка или одно подчёркивание), зачёркнутый ~~, код `…` и блоки ```…```, маркированные и
 * нумерованные списки с вложенностью, цитаты >, горизонтальная линия, таблицы (GFM), ссылки [текст](url) и «голые»
 * http(s)-адреса, переносы строк, экранирование обратной косой чертой.
 */

export type Inline =
  | { t: "text"; v: string }
  | { t: "strong" | "em" | "del"; c: Inline[] }
  | { t: "code"; v: string }
  | { t: "link"; href: string; c: Inline[] }
  | { t: "br" };

export type Align = "left" | "center" | "right" | null;
export interface ListItem { blocks: Block[] }
export type Block =
  | { t: "h"; level: number; c: Inline[] }
  | { t: "p"; c: Inline[] }
  | { t: "list"; ordered: boolean; start: number; items: ListItem[] }
  | { t: "quote"; c: Block[] }
  | { t: "code"; lang: string; v: string }
  | { t: "hr" }
  | { t: "table"; head: Inline[][]; align: Align[]; rows: Inline[][][] };

const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

/** Возвращает безопасный адрес или null (тогда ссылка не создаётся). */
export function safeUrl(raw: string): string | null {
  // eslint-disable-next-line no-control-regex
  const cleaned = raw.replace(/[\u0000-\u001f\u007f\s]+/g, "");
  if (!cleaned) return null;
  try {
    const u = new URL(cleaned);
    return ALLOWED_PROTOCOLS.has(u.protocol) ? cleaned : null;
  } catch {
    return null;
  }
}

const ESCAPABLE = /[\\`*_{}[\]()#+\-.!|~>]/;
const isAlnum = (c: string | undefined) => !!c && /[\p{L}\p{N}]/u.test(c);

function findClosing(s: string, from: number, delim: string): number {
  let j = from;
  while (j < s.length) {
    const k = s.indexOf(delim, j);
    if (k < 0) return -1;
    if (s[k - 1] === "\\") { j = k + 1; continue; }
    return k;
  }
  return -1;
}

function matchLink(s: string, i: number): { end: number; text: string; url: string } | null {
  let depth = 0, j = i;
  for (; j < s.length; j++) {
    if (s[j] === "\\") { j++; continue; }
    if (s[j] === "[") depth++;
    else if (s[j] === "]" && --depth === 0) break;
  }
  if (j >= s.length || s[j + 1] !== "(") return null;
  let k = j + 2, parens = 1;
  for (; k < s.length; k++) {
    if (s[k] === "(") parens++;
    else if (s[k] === ")" && --parens === 0) break;
  }
  if (k >= s.length) return null;
  const inside = s.slice(j + 2, k).trim();
  const url = inside.replace(/^<|>$/g, "").split(/\s+/)[0] ?? "";
  return { end: k + 1, text: s.slice(i + 1, j), url };
}

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => { if (buf) { out.push({ t: "text", v: buf }); buf = ""; } };
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length && ESCAPABLE.test(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
    if (ch === "\n") { flush(); out.push({ t: "br" }); i++; continue; }
    if (ch === "`") {
      let n = 1;
      while (s[i + n] === "`") n++;
      const fence = "`".repeat(n);
      const close = s.indexOf(fence, i + n);
      if (close > 0) { flush(); out.push({ t: "code", v: s.slice(i + n, close).replace(/^ | $/g, "") }); i = close + n; continue; }
      buf += fence; i += n; continue;
    }
    if (ch === "[") {
      const m = matchLink(s, i);
      if (m) {
        flush();
        const href = safeUrl(m.url);
        const inner = parseInline(m.text);
        if (href) out.push({ t: "link", href, c: inner }); else out.push(...inner);
        i = m.end; continue;
      }
    }
    if ((s.startsWith("**", i) || s.startsWith("__", i)) && s[i + 2] && !/\s/.test(s[i + 2])) {
      const d = s.slice(i, i + 2);
      const j = findClosing(s, i + 2, d);
      if (j > i + 2 && !/\s/.test(s[j - 1]) && !(d === "__" && isAlnum(s[j + 2]))) {
        flush(); out.push({ t: "strong", c: parseInline(s.slice(i + 2, j)) }); i = j + 2; continue;
      }
    }
    if (s.startsWith("~~", i) && s[i + 2] && !/\s/.test(s[i + 2])) {
      const j = findClosing(s, i + 2, "~~");
      if (j > i + 2) { flush(); out.push({ t: "del", c: parseInline(s.slice(i + 2, j)) }); i = j + 2; continue; }
    }
    if ((ch === "*" || ch === "_") && s[i + 1] && !/\s/.test(s[i + 1]) && s[i + 1] !== ch) {
      const intraword = ch === "_" && isAlnum(s[i - 1]);
      let j = i + 1;
      for (;;) {
        j = findClosing(s, j, ch);
        if (j < 0 || s[j + 1] !== ch) break;
        j += 2; // это часть двойного разделителя внутри — пропускаем
      }
      if (!intraword && j > i + 1 && !/\s/.test(s[j - 1]) && !(ch === "_" && isAlnum(s[j + 1]))) {
        flush(); out.push({ t: "em", c: parseInline(s.slice(i + 1, j)) }); i = j + 1; continue;
      }
    }
    if ((ch === "h") && /^https?:\/\//i.test(s.slice(i, i + 8)) && !isAlnum(s[i - 1])) {
      const m = /^https?:\/\/[^\s<>"'`]+/i.exec(s.slice(i));
      if (m) {
        let url = m[0];
        while (/[.,;:!?)\]}»”]$/.test(url) && (url.at(-1) !== ")" || (url.match(/\(/g)?.length ?? 0) < (url.match(/\)/g)?.length ?? 0))) url = url.slice(0, -1);
        const href = safeUrl(url);
        if (href) { flush(); out.push({ t: "link", href, c: [{ t: "text", v: url }] }); i += url.length; continue; }
      }
    }
    buf += ch; i++;
  }
  flush();
  return out;
}

// ------------------------------------------------------------------------------------------------ блоки
const RE_FENCE = /^\s{0,3}(```+|~~~+)\s*([\w+#.-]*)\s*$/;
const RE_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RE_HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const RE_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const RE_QUOTE = /^\s{0,3}>\s?(.*)$/;
const RE_TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

const indentOf = (l: string) => l.length - l.trimStart().length;
const isBlank = (l: string) => l.trim() === "";

function splitRow(line: string): string[] {
  let t = line.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|") && !t.endsWith("\\|")) t = t.slice(0, -1);
  const cells: string[] = [];
  let cur = "";
  for (let i = 0; i < t.length; i++) {
    if (t[i] === "\\" && t[i + 1] === "|") { cur += "|"; i++; continue; }
    if (t[i] === "|") { cells.push(cur.trim()); cur = ""; continue; }
    cur += t[i];
  }
  cells.push(cur.trim());
  return cells;
}

const startsBlock = (l: string, next?: string) =>
  RE_FENCE.test(l) || RE_HEADING.test(l) || RE_HR.test(l) || RE_QUOTE.test(l) || RE_ITEM.test(l)
  || (l.includes("|") && next !== undefined && RE_TABLE_SEP.test(next) && next.includes("-"));

export function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isBlank(line)) { i++; continue; }

    let m = RE_FENCE.exec(line);
    if (m) {
      const fence = m[1];
      const body: string[] = [];
      i++;
      while (i < lines.length && !(lines[i].trim().startsWith(fence[0].repeat(fence.length)) && lines[i].trim().replace(new RegExp(`^\\${fence[0]}+`), "") === "")) body.push(lines[i++]);
      i++; // закрывающая ограда (или конец текста)
      blocks.push({ t: "code", lang: m[2], v: body.join("\n") });
      continue;
    }
    if ((m = RE_HEADING.exec(line))) { blocks.push({ t: "h", level: m[1].length, c: parseInline(m[2]) }); i++; continue; }
    if (RE_HR.test(line)) { blocks.push({ t: "hr" }); i++; continue; }

    if (RE_QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && (RE_QUOTE.test(lines[i]) || (!isBlank(lines[i]) && inner.length && !startsBlock(lines[i])))) {
        const q = RE_QUOTE.exec(lines[i]);
        inner.push(q ? q[1] : lines[i]);
        i++;
      }
      blocks.push({ t: "quote", c: parseBlocks(inner) });
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && RE_TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1]).map((c): Align => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null));
      i += 2;
      const rows: Inline[][][] = [];
      while (i < lines.length && !isBlank(lines[i]) && lines[i].includes("|")) {
        const cells = splitRow(lines[i++]);
        rows.push(head.map((_, k) => parseInline(cells[k] ?? "")));
      }
      blocks.push({ t: "table", head: head.map((c) => parseInline(c)), align, rows });
      continue;
    }

    if ((m = RE_ITEM.exec(line))) {
      const base = m[1].length;
      const ordered = /\d/.test(m[2][0]);
      const start = ordered ? parseInt(m[2], 10) : 1;
      const items: ListItem[] = [];
      while (i < lines.length) {
        const im = RE_ITEM.exec(lines[i]);
        if (!im || im[1].length !== base) break;
        if (/\d/.test(im[2][0]) !== ordered) break; // смена вида списка на том же уровне — это уже новый список
        const contentIndent = base + im[2].length + 1;
        const chunk: string[] = [im[3]];
        i++;
        while (i < lines.length) {
          const l = lines[i];
          if (isBlank(l)) {
            // пустая строка не заканчивает список, если дальше идёт продолжение элемента (отступ глубже маркера)
            const nxt = lines.slice(i + 1).find((x) => !isBlank(x));
            if (nxt !== undefined && indentOf(nxt) > base) { chunk.push(""); i++; continue; }
            break;
          }
          if (indentOf(l) > base) { chunk.push(l.slice(Math.min(indentOf(l), contentIndent))); i++; continue; }
          const sib = RE_ITEM.exec(l);
          if (sib && sib[1].length <= base) break;
          if (startsBlock(l)) break;
          chunk.push(l.trimStart()); i++; // «ленивое» продолжение абзаца
        }
        items.push({ blocks: parseBlocks(chunk) });
        // пустая строка между элементами одного списка
        let k = i;
        while (k < lines.length && isBlank(lines[k])) k++;
        if (k > i && k < lines.length) {
          const nm = RE_ITEM.exec(lines[k]);
          if (nm && nm[1].length === base && /\d/.test(nm[2][0]) === ordered) i = k;
        }
      }
      blocks.push({ t: "list", ordered, start, items });
      continue;
    }

    const para: string[] = [];
    while (i < lines.length && !isBlank(lines[i]) && (para.length === 0 || !startsBlock(lines[i], lines[i + 1]))) para.push(lines[i++].trim());
    if (para.length === 0) { para.push(lines[i++].trim()); }
    blocks.push({ t: "p", c: parseInline(para.join("\n")) });
  }
  return blocks;
}

export function parseMarkdown(src: string): Block[] {
  return parseBlocks(src.replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n"));
}

// ------------------------------------------------------------------------------------- простой текст
function inlinePlain(c: Inline[]): string {
  return c.map((x) => {
    switch (x.t) {
      case "text": case "code": return x.v;
      case "br": return "\n";
      case "link": { const t = inlinePlain(x.c); return t === x.href || !t ? x.href : `${t} (${x.href})`; }
      default: return inlinePlain(x.c);
    }
  }).join("");
}

function blocksPlain(blocks: Block[], depth = 0): string[] {
  const out: string[] = [];
  const pad = "  ".repeat(depth);
  for (const b of blocks) {
    switch (b.t) {
      case "h": out.push(inlinePlain(b.c).toUpperCase(), ""); break;
      case "p": out.push(...inlinePlain(b.c).split("\n").map((l) => pad + l), ""); break;
      case "hr": out.push("————————————", ""); break;
      case "code": out.push(...b.v.split("\n").map((l) => pad + "    " + l), ""); break;
      case "quote": out.push(...blocksPlain(b.c, depth).filter((l, i, a) => !(l === "" && i === a.length - 1)).map((l) => (l ? `${pad}> ${l.trimStart()}` : l)), ""); break;
      case "table":
        out.push(b.head.map(inlinePlain).join("\t"), ...b.rows.map((r) => r.map(inlinePlain).join("\t")), ""); break;
      case "list":
        b.items.forEach((it, idx) => {
          const marker = b.ordered ? `${b.start + idx}. ` : "• ";
          const inner = blocksPlain(it.blocks, depth + 1);
          while (inner.length && inner[inner.length - 1] === "") inner.pop();
          if (inner.length === 0) { out.push(pad + marker.trimEnd()); return; }
          inner[0] = pad + marker + inner[0].trimStart();
          out.push(...inner);
        });
        out.push("");
        break;
    }
  }
  return out;
}

/** Обычный текст без разметки — для копирования в письма и документы («Скопировать»). */
export function mdToPlain(src: string): string {
  return blocksPlain(parseMarkdown(src)).join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
