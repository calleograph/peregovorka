// Разбор текста чата на обычный текст и ссылки. Ссылкой считается только явный адрес http(s):// или «www.…» — голые домены и IP не трогаем
// (в чате пишут «srv-db-07», «10.20.30.40», «config.yml»: ложные срабатывания хуже пропуска). Опасные схемы (javascript:, data:) не распознаются в принципе.

export type Chunk = { type: "text"; text: string } | { type: "link"; text: string; href: string };

const URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>"'`]+/giu;
const TRAIL = /[.,;:!?…»”’'"\]}]+$/u;

/** Отделяет от адреса знаки препинания, стоящие после него в предложении, но сохраняет скобки, которые входят в адрес. */
function trimUrl(raw: string): string {
  let url = raw;
  for (;;) {
    const before = url;
    url = url.replace(TRAIL, "");
    if (url.endsWith(")") && (url.match(/\(/g)?.length ?? 0) < (url.match(/\)/g)?.length ?? 0)) url = url.slice(0, -1);
    if (url === before) return url;
  }
}

/** Ссылка → допустимый href (только http/https) либо null. */
export function safeHref(text: string): string | null {
  const candidate = /^www\./i.test(text) ? `https://${text}` : text;
  try {
    const u = new URL(candidate);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch { return null; }
}

export function linkify(text: string): Chunk[] {
  const out: Chunk[] = [];
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    const start = m.index ?? 0;
    const shown = trimUrl(m[0]);
    const href = safeHref(shown);
    if (!href) continue;
    if (start > last) out.push({ type: "text", text: text.slice(last, start) });
    out.push({ type: "link", text: shown, href });
    last = start + shown.length;
  }
  if (last < text.length) out.push({ type: "text", text: text.slice(last) });
  return out;
}
