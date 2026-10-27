"""Оформление «Peregovorka Glass Documents»: Markdown документа → самостоятельная HTML-страница в светлой теме «Стекло».

Содержание и оформление разделены: модель (или код по проверенным пунктам) отдаёт только содержание — Markdown; вёрстка, цвета и компоненты принадлежат этому шаблону
(frontend/public/glassdoc.css, копия — glassdoc_css.py). Модель не может добавить в страницу ни теги, ни стили, ни скрипты: разбор Markdown собственный, весь текст экранируется,
ссылки пропускаются только http/https/mailto. Единственный скрипт в файле — наш собственный (тема, печать, копирование), его хеш прописан в CSP страницы.

Разметка зависит от структуры, а не от слов модели: `# Заголовок` → шапка; сразу под ним строки `**Подпись:** значение` → сведения о встрече; каждый `## Раздел` → панель;
`### Тема` внутри «Обсуждений» → карточка темы; таблицы → адаптивные (на узком экране строки превращаются в карточки); время `[ЧЧ:ММ:СС]` → метка источника.
Пользовательские шаблоны со своей структурой отображаются теми же компонентами и не меняют смысл: порядок и названия разделов остаются авторскими.
"""
from __future__ import annotations

import base64
import hashlib
import re

from .export_docs import Block, Run, _esc, _runs_text, parse_markdown
from .glassdoc_css import GLASSDOC_CSS

KICKER = {"protocol": "Протокол встречи", "summary": "Краткое резюме встречи", "map": "Карта разговора"}
_TS = re.compile(r"(\[\d{1,2}:\d{2}:\d{2}\])")
_SEC_CLASS = (("краткие итоги", "summary"), ("итог", "summary"), ("резюме", "summary"), ("решени", "ok"), ("задач", "info"), ("поручени", "info"),
              ("открыт", "warn"), ("не принят", "muted"), ("предложени", "muted"))
_TOPIC_SECTION = re.compile(r"^(обсуждени|темы|ход)", re.I)


def _chips(text_html: str) -> str:
    """Время реплики в тексте → метка источника. Вызывается для уже экранированного текста: метка не содержит ничего, кроме цифр и двоеточий."""
    return _TS.sub(lambda m: f'<span class="gd-src" data-ts="{m.group(1)[1:-1]}">{m.group(1)}</span>', text_html)


def _runs(runs: list[Run]) -> str:
    out = []
    for r in runs:
        t = _chips(_esc(r.text))
        if r.code:
            t = f"<code>{t}</code>"
        if r.bold:
            t = f"<strong>{t}</strong>"
        if r.italic:
            t = f"<em>{t}</em>"
        if r.href:
            t = f'<a href="{_esc(r.href)}" rel="noopener noreferrer">{t}</a>'
        out.append(t)
    return "".join(out)


def _plain(runs: list[Run]) -> str:
    return _runs_text(runs).strip()


def _list(b: Block) -> str:
    tag = "ol" if b.kind == "ol" else "ul"
    return f"<{tag}>" + "".join(f"<li>{_runs(it.runs)}" + "".join(_list(c) for c in it.children) + "</li>" for it in b.items) + f"</{tag}>"


def _table(b: Block) -> str:
    head, rows = b.rows[0], b.rows[1:]
    labels = [_plain(c) for c in head]
    kinds = ["num" if lb in ("№", "N", "#") else "src" if re.search(r"где в записи|источник", lb, re.I) else "" for lb in labels]
    th = "".join(f'<th class="{k}">{_runs(c)}</th>' if k else f"<th>{_runs(c)}</th>" for c, k in zip(head, kinds))
    body = []
    for r in rows:
        tds = []
        for i, c in enumerate(r):
            k = kinds[i] if i < len(kinds) else ""
            lab = _esc(labels[i]) if i < len(labels) else ""
            tds.append(f'<td{f" class={chr(34)}{k}{chr(34)}" if k else ""} data-label="{lab}">{_runs(c)}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="gd-tablewrap"><table class="gd-table stack"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _is_fact(b: Block) -> tuple[str, list[Run]] | None:
    """`**Дата:** 2026-10-27` → («Дата», остаток строки)."""
    if b.kind != "p" or not b.runs or not b.runs[0].bold:
        return None
    label = b.runs[0].text.strip()
    if not label.endswith(":") or len(label) > 40:
        return None
    rest = list(b.runs[1:])
    if rest:
        rest[0] = Run(rest[0].text.lstrip(), rest[0].bold, rest[0].italic, rest[0].code, rest[0].href)
    return label[:-1].strip(), rest


def _is_disclaimer(b: Block) -> bool:
    return b.kind == "p" and bool(b.runs) and all(r.italic for r in b.runs) and bool(re.match(r"документ собран автоматически", _plain(b.runs), re.I))


def _sec_class(heading: str) -> str:
    h = heading.lower().strip()
    return next((c for key, c in _SEC_CLASS if h.startswith(key) or (len(key) > 5 and key in h)), "")


def _card(h: Block, blocks: list[Block]) -> str:
    parts = [f"<h3>{_runs(h.runs)}</h3>"]
    for b in blocks:
        txt = _plain(b.runs) if b.runs else ""
        if b.kind == "p" and re.match(r"^(чем закончилось|итог|результат)\s*:", txt, re.I):
            parts.append(f'<p class="gd-result">{_runs(b.runs)}</p>')
        elif b.kind == "p" and re.match(r"^(где в записи|источник)\s*:", txt, re.I):
            parts.append(f'<p class="gd-where">{_runs(b.runs)}</p>')
        else:
            parts.append(_block(b))
    return f'<article class="gd-card">{"".join(parts)}</article>'


def _block(b: Block) -> str:
    if b.kind == "p":
        return f"<p>{_runs(b.runs)}</p>"
    if b.kind in ("ul", "ol"):
        return _list(b)
    if b.kind == "table":
        return _table(b)
    if b.kind == "quote":
        return f'<div class="gd-notice"><p>{_runs(b.runs)}</p></div>' if re.search(r"\[проверить\]|⚠", _plain(b.runs)) else f"<blockquote>{_runs(b.runs)}</blockquote>"
    if b.kind == "hr":
        return "<hr>"
    if b.kind == "code":
        return f"<pre>{_esc(b.text)}</pre>"
    if b.kind == "h":
        lvl = min(max(b.level, 3), 4)
        return f"<h{lvl}>{_runs(b.runs)}</h{lvl}>"
    return ""


def render_article(md: str, *, title: str | None = None, kind: str = "protocol") -> tuple[str, str, str]:
    """(HTML шапки и разделов, название документа, примечание-подвал). Только содержимое документа, без обёртки страницы."""
    blocks = parse_markdown(md)
    doc_title = title or KICKER.get(kind, "Документ")
    kicker = KICKER.get(kind, "Документ")
    i = 0
    if blocks and blocks[0].kind == "h" and blocks[0].level == 1:
        doc_title = _plain(blocks[0].runs) or doc_title
        i = 1
    facts: list[tuple[str, list[Run]]] = []
    notices: list[str] = []
    foot = ""
    while i < len(blocks) and not (blocks[i].kind == "h" and blocks[i].level <= 2):
        b = blocks[i]
        f = _is_fact(b)
        if f:
            facts.append(f)
        elif _is_disclaimer(b):
            foot = _plain(b.runs)
        elif b.kind == "quote":
            notices.append(_block(b))
        else:
            break
        i += 1
    if facts and facts[0][0].lower() in ("встреча", "название") and re.search(r"протокол|резюме|карта", doc_title, re.I):
        kicker, doc_title = doc_title, _plain(facts[0][1]) or doc_title        # «Протокол встречи» — малый заголовок, название встречи — крупный
        facts = facts[1:]
    hero_facts = "".join(
        f'<li class="gd-fact{" long" if len(_plain(v)) > 70 else ""}"><b>{_esc(k)}</b><span>{_runs(v)}</span></li>' for k, v in facts)
    hero = (f'<header class="gd-hero"><p class="gd-kicker">{_esc(kicker)}</p><h1 class="gd-title">{_esc(doc_title)}</h1>'
            + (f'<ul class="gd-facts">{hero_facts}</ul>' if hero_facts else "") + "</header>")
    out = [hero] + notices
    # разделы: каждый «## …» — панель; текст до первого «##» (если он есть и это не сведения) — панель без заголовка
    sections: list[tuple[Block | None, list[Block]]] = []
    cur: tuple[Block | None, list[Block]] = (None, [])
    for b in blocks[i:]:
        if _is_disclaimer(b):
            foot = _plain(b.runs)
            continue
        if b.kind == "h" and b.level <= 2:
            if cur[0] is not None or cur[1]:
                sections.append(cur)
            cur = (b, [])
        else:
            cur[1].append(b)
    if cur[0] is not None or cur[1]:
        sections.append(cur)
    for head, body in sections:
        name = _plain(head.runs) if head is not None else ""
        cls = _sec_class(name)
        topics = bool(_TOPIC_SECTION.match(name)) and any(x.kind == "h" and x.level == 3 for x in body)
        parts: list[str] = []
        if topics:
            cards, pre = [], []
            j = 0
            while j < len(body) and not (body[j].kind == "h" and body[j].level == 3):
                pre.append(_block(body[j]))
                j += 1
            while j < len(body):
                h = body[j]
                k = j + 1
                while k < len(body) and not (body[k].kind == "h" and body[k].level == 3):
                    k += 1
                cards.append(_card(h, body[j + 1:k]))
                j = k
            parts = pre + [f'<div class="gd-cards">{"".join(cards)}</div>']
        else:
            parts = [_block(x) for x in body]
        h2 = f"<h2>{_runs(head.runs)}</h2>" if head is not None else ""
        out.append(f'<section class="gd-sec{(" " + cls) if cls else ""}">{h2}{"".join(parts)}</section>')
    return "\n".join(out), doc_title, foot


_SCRIPT = """(function(){var r=document.querySelector('.gd-page'),k='pg-gd-theme';
function set(t){r.setAttribute('data-gd-theme',t);var b=document.querySelector('[data-act=theme]');if(b)b.textContent=t==='dark'?'Светлая тема':'Тёмная тема';}
try{if(localStorage.getItem(k)==='dark')set('dark');}catch(e){}
document.addEventListener('click',function(e){var a=e.target&&e.target.closest?e.target.closest('[data-act]'):null;if(!a)return;var v=a.getAttribute('data-act');
if(v==='theme'){var t=r.getAttribute('data-gd-theme')==='dark'?'light':'dark';set(t);try{localStorage.setItem(k,t);}catch(x){}}
else if(v==='print'){window.print();}
else if(v==='copy'){var s=document.querySelector('.gd-article').innerText;
function done(){a.textContent='Скопировано';setTimeout(function(){a.textContent='Копировать текст';},1800);}
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(s).then(done,function(){});}
else{var t=document.createElement('textarea');t.value=s;document.body.appendChild(t);t.select();try{document.execCommand('copy');done();}catch(x){}document.body.removeChild(t);}}});})();"""
_SCRIPT_HASH = "sha256-" + base64.b64encode(hashlib.sha256(_SCRIPT.encode("utf-8")).digest()).decode("ascii")


CSP = f"default-src 'none'; style-src 'unsafe-inline'; script-src '{_SCRIPT_HASH}'; img-src data:; base-uri 'none'; form-action 'none'"
CSP_HEADER = CSP + "; sandbox allow-scripts allow-modals"        # для ответа сервера: документ не получает доступа к cookie и API приложения даже при открытии в той же вкладке


def page(article_html: str, title: str, foot: str = "", *, kind: str = "protocol") -> str:
    """Самостоятельная страница: стили и скрипт внутри, внешних ресурсов нет; по умолчанию — светлая тема «Стекло» независимо от темы системы."""
    csp = CSP
    note = f'<p class="gd-foot">{_esc(foot)}</p>' if foot else ""
    return (f'<!doctype html>\n<html lang="ru" class="gd-html"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light"><meta http-equiv="Content-Security-Policy" content="{csp}"><title>{_esc(title)}</title>'
            f"<style>{GLASSDOC_CSS}</style></head>\n"
            f'<body><div class="gd gd-page" data-gd-theme="light"><div class="gd-wrap">'
            f'<div class="gd-tools gd-noprint"><button type="button" class="gd-btn" data-act="theme">Тёмная тема</button>'
            f'<button type="button" class="gd-btn" data-act="copy">Копировать текст</button><button type="button" class="gd-btn" data-act="print">Печать</button></div>\n'
            f'<main class="gd-article">\n{article_html}\n</main>\n{note}</div></div>\n<script>{_SCRIPT}</script></body></html>\n')


def to_html(md: str, title: str | None = None, *, kind: str = "protocol") -> str:
    article, doc_title, foot = render_article(md, title=title, kind=kind)
    return page(article, (title or doc_title), foot, kind=kind)
