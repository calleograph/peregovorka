import type { ReactNode } from "react";
import { parseMarkdown, type Block, type Inline } from "../markdown";

function inline(c: Inline[]): ReactNode[] {
  return c.map((x, i): ReactNode => {
    switch (x.t) {
      case "text": return x.v;
      case "br": return <br key={i} />;
      case "code": return <code key={i}>{x.v}</code>;
      case "strong": return <strong key={i}>{inline(x.c)}</strong>;
      case "em": return <em key={i}>{inline(x.c)}</em>;
      case "del": return <del key={i}>{inline(x.c)}</del>;
      case "link": return <a key={i} href={x.href} target="_blank" rel="noopener noreferrer nofollow">{inline(x.c)}</a>;
    }
  });
}

function blocks(list: Block[]): ReactNode[] {
  return list.map((b, i): ReactNode => {
    switch (b.t) {
      case "h": { const H = (`h${Math.min(6, b.level + 1)}`) as "h2"; return <H key={i}>{inline(b.c)}</H>; } // h1 страницы занят заголовком встречи
      case "p": return <p key={i}>{inline(b.c)}</p>;
      case "hr": return <hr key={i} />;
      case "quote": return <blockquote key={i}>{blocks(b.c)}</blockquote>;
      case "code": return <pre key={i}><code>{b.v}</code></pre>;
      case "list": {
        const items = b.items.map((it, k) => <li key={k}>{blocks(it.blocks)}</li>);
        return b.ordered ? <ol key={i} start={b.start}>{items}</ol> : <ul key={i}>{items}</ul>;
      }
      case "table":
        return (
          <div key={i} className="md-table-wrap">
            <table>
              <thead><tr>{b.head.map((h, k) => <th key={k} style={{ textAlign: b.align[k] ?? undefined }}>{inline(h)}</th>)}</tr></thead>
              <tbody>{b.rows.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j} style={{ textAlign: b.align[j] ?? undefined }}>{inline(c)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        );
    }
  });
}

/** Безопасная отрисовка Markdown: только React-элементы, HTML из текста не исполняется (см. markdown.ts). */
export function Markdown({ source }: { source: string }) {
  return <div className="md">{blocks(parseMarkdown(source))}</div>;
}

/** Однострочный Markdown (жирный, код, ссылки) без блочной обёртки — для пунктов списков. */
export function MarkdownInline({ source }: { source: string }) {
  const first = parseMarkdown(source).find((b) => b.t === "p");
  return <>{first && first.t === "p" ? inline(first.c) : source}</>;
}
