"""Оформление «Peregovorka Glass Documents» — КОПИЯ frontend/public/glassdoc.css. Не править вручную: `python3 scripts/sync_glassdoc.py`."""
GLASSDOC_CSS = """/* Peregovorka Glass Documents — общее оформление HTML-материалов встречи (протокол, карта разговора) в светлой теме «Стекло».
   Один файл на всех: страница в приложении, самостоятельный HTML-файл при выгрузке и письма берут отсюда токены и компоненты.
   Копия для сервера — backend/app/services/glassdoc_css.py (собирается scripts/sync_glassdoc.py, совпадение проверяет тест).
   Принципы: светлый воздушный фон с голубым оттенком, мягкие полупрозрачные панели, тонкие границы, небольшие тени, умеренный синий акцент, пастельные цвета категорий.
   Размытия фона (backdrop-filter) нет намеренно: документ должен открываться быстро и на слабом компьютере. Тёмная тема — только по ручному переключателю,
   системная тема на документ не влияет. Все стили под корнем .gd — в приложении они не задевают остальной интерфейс. */
.gd {
  --gd-bg1: #f4f8ff; --gd-bg2: #e8f0fb;
  --gd-panel: rgba(255, 255, 255, .84); --gd-panel-solid: #ffffff; --gd-soft: #eef4fd;
  --gd-line: #d8e3f3; --gd-line-strong: #bccbe3;
  --gd-text: #0f1b3d; --gd-muted: #4c5c7a; --gd-faint: #6b7a96;
  --gd-accent: #2563eb; --gd-accent-ink: #1d4fc4; --gd-accent-soft: #e3edff;
  --gd-ok: #1f9d55; --gd-ok-bg: #dcf3e6; --gd-ok-ink: #14693a;
  --gd-warn: #d19a2a; --gd-warn-bg: #fbefcf; --gd-warn-ink: #6b4e0e;
  --gd-note-bg: #eef1f8; --gd-note-ink: #44506a;
  --gd-shadow: 0 1px 2px rgba(20, 50, 120, .05), 0 12px 26px -18px rgba(20, 50, 120, .28);
  --gd-radius: 20px; --gd-radius-sm: 12px;
  --gd-font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif;
  /* категории тем (карта разговора) — пастель: фон, граница, текст; красного среди них нет, цвет не означает «хорошо/плохо» */
  --gd-infra-bg: #e3eeff; --gd-infra-bd: #a9c6f5; --gd-infra-ink: #1f4e9c;
  --gd-dev-bg: #daf3ee; --gd-dev-bd: #93d6c9; --gd-dev-ink: #12685d;
  --gd-security-bg: #e2f4e4; --gd-security-bd: #9cd3a4; --gd-security-ink: #1f6a2b;
  --gd-org-bg: #ece7fb; --gd-org-bd: #c0b0ee; --gd-org-ink: #523493;
  --gd-finance-bg: #f8eed6; --gd-finance-bd: #e1c98e; --gd-finance-ink: #74570f;
  --gd-process-bg: #e4ebf4; --gd-process-bd: #aebfd6; --gd-process-ink: #37526f;
  --gd-data-bg: #f6ebdf; --gd-data-bd: #dcc1a0; --gd-data-ink: #6d471a;
  --gd-general-bg: #edf0f5; --gd-general-bd: #c4ccd9; --gd-general-ink: #485369;
  color: var(--gd-text); font: 15px/1.55 var(--gd-font); -webkit-text-size-adjust: 100%; text-size-adjust: 100%;
}
.gd[data-gd-theme="dark"] {
  --gd-bg1: #0f1626; --gd-bg2: #121c31;
  --gd-panel: rgba(26, 36, 58, .86); --gd-panel-solid: #1a243a; --gd-soft: #202c46;
  --gd-line: #2c3a58; --gd-line-strong: #3b4c70;
  --gd-text: #e6ebf7; --gd-muted: #a5b1cb; --gd-faint: #8593af;
  --gd-accent: #7aa2ff; --gd-accent-ink: #a9c3ff; --gd-accent-soft: #1f2f55;
  --gd-ok: #4cc283; --gd-ok-bg: #153626; --gd-ok-ink: #8fdcb2;
  --gd-warn: #e0b04f; --gd-warn-bg: #3a2f14; --gd-warn-ink: #f0d58f;
  --gd-note-bg: #222d45; --gd-note-ink: #b6c1da;
  --gd-shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 12px 26px -18px rgba(0, 0, 0, .6);
  --gd-infra-bg: #1c2f52; --gd-infra-bd: #3f6bb8; --gd-infra-ink: #bcd3fb;
  --gd-dev-bg: #163530; --gd-dev-bd: #2f8b7e; --gd-dev-ink: #a9e8dd;
  --gd-security-bg: #1c3421; --gd-security-bd: #3f8c4b; --gd-security-ink: #b3e5bb;
  --gd-org-bg: #2d2549; --gd-org-bd: #6f58b8; --gd-org-ink: #d6cafa;
  --gd-finance-bg: #3b3118; --gd-finance-bd: #96792a; --gd-finance-ink: #f0dca4;
  --gd-process-bg: #222e3e; --gd-process-bd: #51698a; --gd-process-ink: #c0cfe5;
  --gd-data-bg: #39291b; --gd-data-bd: #8f6a3d; --gd-data-ink: #ecd2b0;
  --gd-general-bg: #272d39; --gd-general-bd: #596273; --gd-general-ink: #c9cfdb;
  color-scheme: dark;
}
.gd, .gd *, .gd *::before, .gd *::after { box-sizing: border-box; }

/* ---------------------------------------------------------------- страница (самостоятельный файл) */
html.gd-html, .gd-html body { margin: 0; background: #e8f0fb; }
.gd-page { min-height: 100vh; padding: 28px 16px 56px; background: linear-gradient(180deg, var(--gd-bg1) 0%, var(--gd-bg2) 100%); }
.gd-page[data-gd-theme="dark"] { color-scheme: dark; }
.gd-wrap { max-width: 920px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
.gd-wrap.wide { max-width: 1240px; }
.gd-article { display: flex; flex-direction: column; gap: 16px; }

/* ---------------------------------------------------------------- шапка */
.gd-tools { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.gd-btn { font: inherit; font-size: 13px; line-height: 1; padding: 8px 14px; border-radius: 999px; border: 1px solid var(--gd-line); background: var(--gd-panel); color: var(--gd-muted); cursor: pointer; transition: background .15s, color .15s, border-color .15s; }
.gd-btn:hover { background: var(--gd-panel-solid); color: var(--gd-accent-ink); border-color: var(--gd-line-strong); }
.gd-btn:focus-visible, .gd a:focus-visible, .gd button:focus-visible, .gd summary:focus-visible { outline: 2px solid var(--gd-accent); outline-offset: 2px; }
.gd-hero { position: relative; padding: 26px 28px 22px; border-radius: var(--gd-radius); border: 1px solid var(--gd-line); box-shadow: var(--gd-shadow); overflow: hidden;
  background: linear-gradient(135deg, var(--gd-panel-solid) 0%, var(--gd-accent-soft) 160%); }
.gd-hero::after { content: ""; position: absolute; right: -60px; top: -80px; width: 260px; height: 260px; border-radius: 50%; background: radial-gradient(closest-side, rgba(37, 99, 235, .12), rgba(37, 99, 235, 0)); pointer-events: none; }
.gd-kicker { margin: 0 0 6px; font-size: 12.5px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--gd-accent-ink); }
.gd-title { margin: 0; font-size: clamp(24px, 3.4vw, 34px); line-height: 1.15; font-weight: 800; letter-spacing: -.02em; overflow-wrap: anywhere; }
.gd-facts { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 0; padding: 0; list-style: none; position: relative; z-index: 1; }
.gd-fact { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: 6px; max-width: 100%; padding: 6px 12px; border-radius: 999px; background: var(--gd-panel); border: 1px solid var(--gd-line); font-size: 13.5px; }
.gd-fact b { font-weight: 600; color: var(--gd-muted); font-size: 12.5px; }
.gd-fact.long { border-radius: var(--gd-radius-sm); flex: 1 1 100%; }
.gd-fact span { overflow-wrap: anywhere; }

/* ---------------------------------------------------------------- разделы */
.gd-sec { padding: 20px 24px 22px; border-radius: var(--gd-radius); border: 1px solid var(--gd-line); background: var(--gd-panel); box-shadow: var(--gd-shadow); }
.gd-sec > h2 { display: flex; align-items: center; gap: 10px; margin: 0 0 12px; font-size: 18px; line-height: 1.25; font-weight: 700; letter-spacing: -.01em; }
.gd-sec > h2::before { content: ""; flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--gd-sec, var(--gd-accent)); box-shadow: 0 0 0 4px color-mix(in srgb, var(--gd-sec, var(--gd-accent)) 18%, transparent); }
.gd-sec.ok { --gd-sec: var(--gd-ok); }
.gd-sec.info { --gd-sec: var(--gd-accent); }
.gd-sec.warn { --gd-sec: var(--gd-warn); }
.gd-sec.muted { --gd-sec: var(--gd-faint); }
.gd-sec.summary { background: linear-gradient(135deg, var(--gd-panel-solid), var(--gd-accent-soft)); }
.gd-sec.summary p { font-size: 16.5px; line-height: 1.6; }
.gd-sec h3 { margin: 18px 0 6px; font-size: 15.5px; font-weight: 700; }
.gd-sec h4 { margin: 14px 0 4px; font-size: 12.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--gd-muted); }
.gd p { margin: 0 0 10px; overflow-wrap: anywhere; }
.gd p:last-child { margin-bottom: 0; }
.gd a { color: var(--gd-accent-ink); text-underline-offset: 2px; }
.gd code { font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; padding: 1px 6px; border-radius: 6px; background: var(--gd-soft); }
.gd pre { margin: 8px 0; padding: 12px 14px; overflow: auto; border-radius: var(--gd-radius-sm); background: var(--gd-soft); font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.gd hr { border: 0; border-top: 1px solid var(--gd-line); margin: 16px 0; }
.gd blockquote { margin: 8px 0; padding: 8px 12px; border-left: 3px solid var(--gd-line-strong); background: var(--gd-soft); border-radius: 0 var(--gd-radius-sm) var(--gd-radius-sm) 0; color: var(--gd-muted); }

/* темы обсуждения — карточки */
.gd-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 330px), 1fr)); gap: 12px; margin-top: 4px; }
.gd-card { padding: 14px 16px 12px; border-radius: 16px; border: 1px solid var(--gd-line); background: var(--gd-panel-solid); }
.gd-card > h3 { margin: 0 0 6px; font-size: 15.5px; line-height: 1.3; }
.gd-card p { font-size: 14.5px; }
.gd-card .gd-result { margin-top: 8px; padding: 6px 10px; border-radius: 10px; background: var(--gd-soft); color: var(--gd-text); font-size: 13.5px; }
.gd-card .gd-result b { color: var(--gd-muted); font-weight: 600; }

/* списки и таблицы */
.gd-sec ul, .gd-sec ol { margin: 6px 0 10px; padding-left: 22px; }
.gd-sec li { margin: 3px 0; overflow-wrap: anywhere; }
.gd-sec.ok ol, .gd-sec.warn ol, .gd-sec.muted ol { list-style: none; padding: 0; counter-reset: gd-n; }
.gd-sec.ok ol > li, .gd-sec.warn ol > li, .gd-sec.muted ol > li { counter-increment: gd-n; position: relative; padding: 8px 12px 8px 46px; margin: 6px 0; border-radius: 14px; border: 1px solid var(--gd-line); background: var(--gd-panel-solid); }
.gd-sec.ok ol > li::before, .gd-sec.warn ol > li::before, .gd-sec.muted ol > li::before { content: counter(gd-n); position: absolute; left: 12px; top: 9px; width: 24px; height: 24px; display: grid; place-items: center; border-radius: 50%; font-size: 12px; font-weight: 700; color: var(--gd-ok-ink); background: var(--gd-ok-bg); }
.gd-sec.warn ol > li::before { color: var(--gd-warn-ink); background: var(--gd-warn-bg); }
.gd-sec.muted ol > li::before { color: var(--gd-note-ink); background: var(--gd-note-bg); }
.gd-tablewrap { margin: 6px -4px 4px; padding: 0 4px; overflow-x: auto; }
.gd-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; border: 1px solid var(--gd-line); border-radius: 14px; overflow: hidden; background: var(--gd-panel-solid); }
.gd-table th { text-align: left; font-size: 12px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--gd-muted); background: var(--gd-soft); padding: 9px 12px; border-bottom: 1px solid var(--gd-line); vertical-align: bottom; }
.gd-table td { padding: 10px 12px; vertical-align: top; border-bottom: 1px solid var(--gd-line); overflow-wrap: anywhere; }
.gd-table tr:last-child td { border-bottom: 0; }
.gd-table td.num, .gd-table th.num { width: 1%; white-space: nowrap; color: var(--gd-faint); }
.gd-table td.src { font-size: 12.5px; color: var(--gd-muted); }
.gd-sec.info .gd-table th:first-child { border-top-left-radius: 14px; }

/* метки, источники, примечания */
.gd-src, .gd-chip { display: inline-block; max-width: 100%; padding: 2px 9px; border-radius: 999px; font-size: 12.5px; line-height: 1.5; background: var(--gd-soft); border: 1px solid var(--gd-line); color: var(--gd-muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
button.gd-src { font-family: inherit; cursor: pointer; }
button.gd-src:hover { border-color: var(--gd-accent); color: var(--gd-accent-ink); background: var(--gd-accent-soft); }
.gd-card .gd-where { margin-top: 8px; font-size: 12.5px; color: var(--gd-faint); }
.gd-pill { display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.gd-pill.ok { background: var(--gd-ok-bg); color: var(--gd-ok-ink); }
.gd-pill.ai { background: var(--gd-note-bg); color: var(--gd-note-ink); }
.gd-pill.warn { background: var(--gd-warn-bg); color: var(--gd-warn-ink); }
.gd-notice { padding: 12px 16px; border-radius: 14px; border: 1px solid color-mix(in srgb, var(--gd-warn) 45%, var(--gd-line)); background: var(--gd-warn-bg); color: var(--gd-warn-ink); font-size: 14px; }
.gd-notice p { margin: 0; }
.gd-foot { padding: 0 8px; color: var(--gd-faint); font-size: 12.5px; text-align: center; }
.gd-empty { color: var(--gd-faint); font-style: italic; }
.gd details.gd-more { margin-top: 8px; font-size: 13px; color: var(--gd-muted); }
.gd details.gd-more summary { cursor: pointer; }

/* ---------------------------------------------------------------- узкое окно */
@media (max-width: 640px) {
  .gd-page { padding: 14px 10px 36px; }
  .gd-hero { padding: 20px 18px 18px; }
  .gd-sec { padding: 16px 16px 18px; }
  .gd-table.stack, .gd-table.stack tbody, .gd-table.stack tr, .gd-table.stack td { display: block; width: 100%; }
  .gd-table.stack thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .gd-table.stack { border: 0; background: transparent; }
  .gd-table.stack tr { margin: 0 0 10px; padding: 8px 12px; border: 1px solid var(--gd-line); border-radius: 14px; background: var(--gd-panel-solid); }
  .gd-table.stack td { padding: 4px 0; border: 0; }
  .gd-table.stack td::before { content: attr(data-label); display: block; font-size: 11.5px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--gd-faint); }
  .gd-table.stack td.num { display: none; }
}
@media (prefers-reduced-motion: reduce) { .gd *, .gd *::before, .gd *::after { transition: none !important; animation: none !important; } }

/* ---------------------------------------------------------------- печать: чистый белый лист, без фонов, теней и кнопок */
@media print {
  html.gd-html, .gd-html body, .gd-page { background: #fff !important; }
  .gd, .gd[data-gd-theme="dark"] { --gd-panel: #fff; --gd-panel-solid: #fff; --gd-soft: #f4f6fa; --gd-line: #cfd6e4; --gd-line-strong: #aeb8cc; --gd-text: #111; --gd-muted: #333; --gd-faint: #555;
    --gd-accent-soft: #fff; --gd-shadow: none; color: #111; }
  .gd-page { padding: 0; min-height: 0; }
  .gd-wrap { max-width: none; gap: 10px; }
  .gd-tools, .gd-hero::after, .gd button, .gd .gd-noprint { display: none !important; }
  .gd-hero, .gd-sec, .gd-sec.summary { background: #fff; box-shadow: none; border-color: #cfd6e4; }
  .gd-sec { break-inside: auto; }
  .gd-card, .gd-table tr, .gd-sec ol > li { break-inside: avoid; }
  .gd-sec > h2 { break-after: avoid; }
  .gd-tablewrap { overflow: visible; }
  .gd a { color: #111; text-decoration: none; }
  @page { margin: 14mm; }
}
"""
