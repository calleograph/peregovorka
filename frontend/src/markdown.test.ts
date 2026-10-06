import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "./components/Markdown";
import rendererSource from "./components/Markdown.tsx?raw";
import { mdToPlain, parseInline, parseMarkdown, safeUrl } from "./markdown";

const html = (src: string) => renderToStaticMarkup(createElement(Markdown, { source: src }));

describe("безопасность", () => {
  it("HTML из текста не исполняется и экранируется", () => {
    const out = html('<script>alert(1)</script> <img src=x onerror=alert(1)> <b>x</b>');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<b>x</b>");
    expect(out).toContain("&lt;script&gt;");
  });

  it("опасные схемы ссылок отбрасываются, остаётся подпись", () => {
    for (const u of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", " java\tscript:alert(1)", "data:text/html,<script>1</script>", "vbscript:x", "file:///etc/passwd", "//evil.example", "/relative"]) {
      expect(safeUrl(u)).toBeNull();
      const out = html(`[нажми](${u})`);
      expect(out).not.toContain("<a ");
      expect(out).toContain("нажми");
    }
  });

  it("http, https и mailto разрешены; ссылки открываются безопасно", () => {
    expect(safeUrl("https://example.org/a?b=1")).toBe("https://example.org/a?b=1");
    expect(safeUrl("mailto:ivan@example.org")).toBe("mailto:ivan@example.org");
    const out = html("[сайт](https://example.org)");
    expect(out).toContain('href="https://example.org"');
    expect(out).toContain('rel="noopener noreferrer nofollow"');
    expect(out).toContain('target="_blank"');
  });

  it("атрибуты не могут «выпрыгнуть» из href", () => {
    const out = html('[x](https://a.b/" onmouseover="alert(1))');
    expect(out).not.toMatch(/onmouseover=/);
  });

  it("код и ссылки внутри кода не интерпретируются", () => {
    const out = html("`<b>[x](javascript:1)</b>`");
    expect(out).toContain("<code>&lt;b&gt;[x](javascript:1)&lt;/b&gt;</code>");
  });

  it("dangerouslySetInnerHTML не используется", () => {
    expect(rendererSource).not.toContain("dangerouslySetInnerHTML");
  });
});

describe("разметка", () => {
  it("заголовки, жирный, курсив, зачёркнутый, код", () => {
    const out = html("# Тема\n\n**жирный** и *курсив* и ~~старое~~ и `код`");
    expect(out).toContain("<h2>Тема</h2>");
    expect(out).toContain("<strong>жирный</strong>");
    expect(out).toContain("<em>курсив</em>");
    expect(out).toContain("<del>старое</del>");
    expect(out).toContain("<code>код</code>");
  });

  it("вложенные списки и смена вида списка", () => {
    const b = parseMarkdown("- a\n  - вложенный\n    - ещё глубже\n- b\n\n1. один\n2. два");
    expect(b.map((x) => x.t)).toEqual(["list", "list"]);
    const ul = b[0] as Extract<(typeof b)[number], { t: "list" }>;
    expect(ul.ordered).toBe(false);
    expect(ul.items).toHaveLength(2);
    const nested = ul.items[0].blocks.find((x) => x.t === "list");
    expect(nested).toBeTruthy();
    expect(html("- a\n  - б\n- в")).toBe('<div class="md"><ul><li><p>a</p><ul><li><p>б</p></li></ul></li><li><p>в</p></li></ul></div>');
    const ol = b[1] as Extract<(typeof b)[number], { t: "list" }>;
    expect(ol.ordered).toBe(true);
    expect(ol.items).toHaveLength(2);
  });

  it("нумерация с произвольного номера сохраняется", () => {
    expect(html("3. три\n4. четыре")).toContain('<ol start="3">');
  });

  it("таблица с выравниванием", () => {
    const out = html("| Ответственный | Срок |\n|:--|--:|\n| Иван | 01.11 |\n| Пётр \\| Сидоров | завтра |");
    expect(out).toContain("<table>");
    expect(out).toContain("<th");
    expect(out).toContain("Иван");
    expect(out).toContain("Пётр | Сидоров");
    expect(out).toContain("text-align:right");
  });

  it("цитата, горизонтальная линия, блок кода", () => {
    const out = html("> цитата\n> вторая\n\n---\n\n```js\nconst a = 1 < 2;\n```");
    expect(out).toContain("<blockquote>");
    expect(out).toContain("<hr/>");
    expect(out).toContain("<pre><code>const a = 1 &lt; 2;</code></pre>");
  });

  it("переносы строк в абзаце сохраняются", () => {
    expect(html("строка 1\nстрока 2")).toContain("строка 1<br/>строка 2");
  });

  it("«голые» адреса становятся ссылками без хвостовой пунктуации", () => {
    const i = parseInline("см. https://example.org/doc. Далее");
    const link = i.find((x) => x.t === "link");
    expect(link && link.t === "link" && link.href).toBe("https://example.org/doc");
  });

  it("экранирование и подчёркивания внутри слов", () => {
    expect(html("2 \\* 3 и snake_case_name")).toContain("2 * 3 и snake_case_name");
  });

  it("незакрытые разделители не ломают текст", () => {
    expect(html("**не закрыто и `код")).toContain("**не закрыто и `код");
  });
});

describe("простой текст для копирования", () => {
  it("убирает разметку, сохраняет структуру", () => {
    const t = mdToPlain("# Протокол\n\n**Решение:** принять\n\n- пункт 1\n  - подпункт\n- пункт 2\n\n1. один\n2. два\n\n| К | Ч |\n|---|---|\n| Иван | отчёт |\n\n> важно\n\n[сайт](https://example.org)");
    expect(t).toContain("ПРОТОКОЛ");
    expect(t).toContain("Решение: принять");
    expect(t).toContain("• пункт 1");
    expect(t).toContain("  • подпункт");
    expect(t).toContain("1. один");
    expect(t).toContain("Иван\tотчёт");
    expect(t).toContain("> важно");
    expect(t).toContain("сайт (https://example.org)");
    expect(t).not.toMatch(/\*\*|\|---|^#/m);
  });
});
