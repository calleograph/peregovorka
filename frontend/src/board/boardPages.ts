// Страницы схемы draw.io. Редактор при выгрузке в PNG/SVG берёт только текущую страницу, поэтому у многостраничной схемы каждую страницу выгружаем отдельно:
// файл `.drawio` режется на файлы из одной страницы (разбор строковый — формат <mxfile><diagram …>…</diagram></mxfile> простой, DOM для этого не нужен).

export interface BoardPage { id: string; name: string; xml: string }

const unescapeXml = (s: string) => s
  .replace(/&#x([0-9a-f]+);/gi, (_, h: string) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/&#(\d+);/g, (_, d: string) => String.fromCodePoint(Number(d)))
  .replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

/** Страницы файла draw.io; пустой массив — это не файл схемы (нечего выгружать по страницам). */
export function splitPages(xml: string): BoardPage[] {
  const open = /<mxfile\b[^>]*>/.exec(xml);
  const blocks = xml.match(/<diagram\b[^>]*>[\s\S]*?<\/diagram>/g) ?? [];
  if (!open || blocks.length === 0) return [];
  return blocks.map((block, i) => {
    const head = /<diagram\b([^>]*)>/.exec(block)![1];
    const attr = (n: string) => new RegExp(`\\s${n}="([^"]*)"`).exec(head)?.[1];
    return { id: unescapeXml(attr("id") ?? String(i + 1)), name: unescapeXml(attr("name") ?? "").trim() || `Страница ${i + 1}`, xml: `${open[0]}${block}</mxfile>` };
  });
}

/** Часть имени файла: без символов, недопустимых в Windows и на мобильных, не длиннее 80 знаков. */
export function safeFileName(s: string): string {
  const t = s.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").replace(/\s+/g, " ").trim().replace(/^\.+/, "");
  return (t || "схема").slice(0, 80);
}
