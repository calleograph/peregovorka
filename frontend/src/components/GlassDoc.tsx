import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { parseMarkdown, type Block, type Inline } from "../markdown";

/** Документ в оформлении «Peregovorka Glass Documents» (frontend/public/glassdoc.css): Markdown → шапка, панели разделов, карточки тем, таблицы, метки источников.
 *  Разметка и правила разбора — те же, что у серверного шаблона (backend/app/services/glass_html.py): страница в приложении и выгруженный HTML выглядят одинаково.
 *  HTML из текста не исполняется: только React-элементы. */

export type GdKind = "protocol" | "summary";
const KICKER: Record<GdKind, string> = { protocol: "Протокол встречи", summary: "Краткое резюме встречи" };
const TS = /(\[\d{1,2}:\d{2}:\d{2}\])/;
const SEC_CLASS: [string, string][] = [["краткие итоги", "summary"], ["итог", "summary"], ["резюме", "summary"], ["решени", "ok"], ["задач", "info"], ["поручени", "info"], ["открыт", "warn"], ["не принят", "muted"], ["предложени", "muted"]];
const TOPIC_SECTION = /^(обсуждени|темы|ход)/i;

export function plainOf(c: Inline[]): string {
  return c.map((x) => (x.t === "text" || x.t === "code" ? x.v : x.t === "br" ? " " : plainOf(x.c))).join("").trim();
}
export function secClass(heading: string): string {
  const h = heading.toLowerCase().trim();
  return SEC_CLASS.find(([k]) => h.startsWith(k) || (k.length > 5 && h.includes(k)))?.[1] ?? "";
}
export function isFact(b: Block): { label: string; value: Inline[] } | null {
  if (b.t !== "p" || b.c[0]?.t !== "strong") return null;
  const label = plainOf(b.c[0].c);
  if (!label.endsWith(":") || label.length > 40) return null;
  const rest = b.c.slice(1);
  if (rest[0]?.t === "text") rest[0] = { t: "text", v: rest[0].v.trimStart() };
  return { label: label.slice(0, -1).trim(), value: rest };
}
const isDisclaimer = (b: Block) => b.t === "p" && b.c.length > 0 && b.c.every((x) => x.t === "em") && /^документ собран автоматически/i.test(plainOf(b.c));

type Goto = ((clock: string) => void) | undefined;

function text(v: string, key: number, goto: Goto): ReactNode {
  return v.split(TS).map((part, i): ReactNode => {
    const m = /^\[(\d{1,2}:\d{2}:\d{2})\]$/.exec(part);
    if (!m) return part;
    const clock = m[1];
    return goto
      ? <button key={`${key}-${i}`} type="button" className="gd-src" data-ts={clock} title="Показать это место в стенограмме" onClick={() => goto(clock)}>{part}</button>
      : <span key={`${key}-${i}`} className="gd-src" data-ts={clock}>{part}</span>;
  });
}
function inline(c: Inline[], goto: Goto): ReactNode[] {
  return c.map((x, i): ReactNode => {
    switch (x.t) {
      case "text": return text(x.v, i, goto);
      case "br": return <br key={i} />;
      case "code": return <code key={i}>{x.v}</code>;
      case "strong": return <strong key={i}>{inline(x.c, goto)}</strong>;
      case "em": return <em key={i}>{inline(x.c, goto)}</em>;
      case "del": return <del key={i}>{inline(x.c, goto)}</del>;
      case "link": return <a key={i} href={x.href} target="_blank" rel="noopener noreferrer nofollow">{inline(x.c, goto)}</a>;
    }
  });
}

function block(b: Block, key: number, goto: Goto): ReactNode {
  switch (b.t) {
    case "p": return <p key={key}>{inline(b.c, goto)}</p>;
    case "hr": return <hr key={key} />;
    case "code": return <pre key={key}>{b.v}</pre>;
    case "quote": {
      const t = b.c.map((x) => (x.t === "p" ? plainOf(x.c) : "")).join(" ");
      return /\[проверить\]|⚠/.test(t) ? <div key={key} className="gd-notice">{b.c.map((x, i) => block(x, i, goto))}</div> : <blockquote key={key}>{b.c.map((x, i) => block(x, i, goto))}</blockquote>;
    }
    case "list": {
      const items = b.items.map((it, k) => <li key={k}>{it.blocks.map((x, i) => (x.t === "p" ? <span key={i}>{inline(x.c, goto)}</span> : block(x, i, goto)))}</li>);
      return b.ordered ? <ol key={key} start={b.start}>{items}</ol> : <ul key={key}>{items}</ul>;
    }
    case "table": {
      const labels = b.head.map(plainOf);
      const kinds = labels.map((l) => (["№", "N", "#"].includes(l) ? "num" : /где в записи|источник/i.test(l) ? "src" : ""));
      return (
        <div key={key} className="gd-tablewrap">
          <table className="gd-table stack">
            <thead><tr>{b.head.map((h, k) => <th key={k} className={kinds[k] || undefined}>{inline(h, goto)}</th>)}</tr></thead>
            <tbody>{b.rows.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j} className={kinds[j] || undefined} data-label={labels[j]}>{inline(c, goto)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      );
    }
    case "h": { const H = (`h${Math.min(Math.max(b.level, 3), 4)}`) as "h3"; return <H key={key}>{inline(b.c, goto)}</H>; }
  }
}

function card(h: Block & { t: "h" }, body: Block[], key: number, goto: Goto): ReactNode {
  return (
    <article key={key} className="gd-card">
      <h3>{inline(h.c, goto)}</h3>
      {body.map((b, i) => {
        const t = b.t === "p" ? plainOf(b.c) : "";
        if (b.t === "p" && /^(чем закончилось|итог|результат)\s*:/i.test(t)) return <p key={i} className="gd-result">{inline(b.c, goto)}</p>;
        if (b.t === "p" && /^(где в записи|источник)\s*:/i.test(t)) return <p key={i} className="gd-where">{inline(b.c, goto)}</p>;
        return block(b, i, goto);
      })}
    </article>
  );
}

/** Содержимое документа без обёртки (то, что внутри <main class="gd-article">). */
export function GlassArticle({ source, title, kind = "protocol", onGoto }: { source: string; title?: string; kind?: GdKind; onGoto?: Goto }) {
  const blocks = parseMarkdown(source);
  let docTitle = title || KICKER[kind];
  let kicker = KICKER[kind];
  let i = 0;
  if (blocks[0]?.t === "h" && blocks[0].level === 1) { docTitle = plainOf(blocks[0].c) || docTitle; i = 1; }
  let facts: { label: string; value: Inline[] }[] = [];
  const notices: ReactNode[] = [];
  let foot = "";
  while (i < blocks.length && !(blocks[i].t === "h" && (blocks[i] as { level: number }).level <= 2)) {
    const b = blocks[i];
    const f = isFact(b);
    if (f) facts.push(f);
    else if (isDisclaimer(b)) foot = plainOf((b as { c: Inline[] }).c);
    else if (b.t === "quote") notices.push(block(b, 1000 + i, onGoto));
    else break;
    i++;
  }
  if (facts[0] && ["встреча", "название"].includes(facts[0].label.toLowerCase()) && /протокол|резюме|карта/i.test(docTitle)) {
    kicker = docTitle; docTitle = plainOf(facts[0].value) || docTitle; facts = facts.slice(1);
  }
  const sections: { head: (Block & { t: "h" }) | null; body: Block[] }[] = [];
  let cur: { head: (Block & { t: "h" }) | null; body: Block[] } = { head: null, body: [] };
  for (const b of blocks.slice(i)) {
    if (isDisclaimer(b)) { foot = plainOf((b as { c: Inline[] }).c); continue; }
    if (b.t === "h" && b.level <= 2) { if (cur.head || cur.body.length) sections.push(cur); cur = { head: b, body: [] }; } else cur.body.push(b);
  }
  if (cur.head || cur.body.length) sections.push(cur);
  return (
    <>
      <header className="gd-hero">
        <p className="gd-kicker">{kicker}</p>
        <h1 className="gd-title">{docTitle}</h1>
        {facts.length > 0 && <ul className="gd-facts">{facts.map((f, k) => <li key={k} className={`gd-fact${plainOf(f.value).length > 70 ? " long" : ""}`}><b>{f.label}</b><span>{inline(f.value, onGoto)}</span></li>)}</ul>}
      </header>
      {notices}
      {sections.map((s, n) => {
        const name = s.head ? plainOf(s.head.c) : "";
        const cls = secClass(name);
        const topics = TOPIC_SECTION.test(name) && s.body.some((x) => x.t === "h" && x.level === 3);
        let parts: ReactNode[];
        if (topics) {
          const pre: ReactNode[] = [];
          let j = 0;
          while (j < s.body.length && !(s.body[j].t === "h" && (s.body[j] as { level: number }).level === 3)) { pre.push(block(s.body[j], j, onGoto)); j++; }
          const cards: ReactNode[] = [];
          while (j < s.body.length) {
            const h = s.body[j] as Block & { t: "h" };
            let k = j + 1;
            while (k < s.body.length && !(s.body[k].t === "h" && (s.body[k] as { level: number }).level === 3)) k++;
            cards.push(card(h, s.body.slice(j + 1, k), j, onGoto));
            j = k;
          }
          parts = [...pre, <div key="cards" className="gd-cards">{cards}</div>];
        } else parts = s.body.map((x, k) => block(x, k, onGoto));
        return <section key={n} className={`gd-sec${cls ? ` ${cls}` : ""}`}>{s.head && <h2>{inline(s.head.c, onGoto)}</h2>}{parts}</section>;
      })}
      {foot && <p className="gd-foot">{foot}</p>}
    </>
  );
}

const THEME_KEY = "pg-gd-theme";
function savedTheme(): "light" | "dark" {
  try { return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light"; } catch { return "light"; }
}

/** Документ целиком: панель «Тема / Печать», светлое оформление «Стекло» по умолчанию (тема системы и приложения на документ не влияет), чистая печать. */
export default function GlassDoc({ source, title, kind = "protocol", onGoto, extraTools }: { source: string; title?: string; kind?: GdKind; onGoto?: Goto; extraTools?: ReactNode }) {
  const [theme, setTheme] = useState<"light" | "dark">(savedTheme);
  const [printing, setPrinting] = useState(false);
  const flip = () => { const t = theme === "dark" ? "light" : "dark"; setTheme(t); try { localStorage.setItem(THEME_KEY, t); } catch { /* без хранилища тема просто не запоминается */ } };

  // Печать: отдельный чистый лист поверх страницы (интерфейс приложения на печать не попадает); после печати лист убирается
  useEffect(() => {
    if (!printing) return;
    const root = document.documentElement;
    root.classList.add("gd-printing");
    const done = () => { root.classList.remove("gd-printing"); setPrinting(false); };
    window.addEventListener("afterprint", done, { once: true });
    const t = window.setTimeout(() => window.print(), 60);
    return () => { window.clearTimeout(t); window.removeEventListener("afterprint", done); root.classList.remove("gd-printing"); };
  }, [printing]);

  return (
    <div className="gd gd-inapp" data-gd-theme={theme}>
      <div className="gd-tools gd-noprint">
        {extraTools}
        <button type="button" className="gd-btn" onClick={flip}>{theme === "dark" ? "Светлая тема" : "Тёмная тема"}</button>
        <button type="button" className="gd-btn" onClick={() => setPrinting(true)}>Печать</button>
      </div>
      <main className="gd-article"><GlassArticle source={source} title={title} kind={kind} onGoto={onGoto} /></main>
      {printing && createPortal(<div className="gd gd-print-sheet" data-gd-theme="light"><main className="gd-article"><GlassArticle source={source} title={title} kind={kind} /></main></div>, document.body)}
    </div>
  );
}
