"""Оформление «Peregovorka Glass Documents»: одинаковая копия CSS на сервере и в приложении, безопасность (экранирование, ссылки, политика страницы, единственный свой скрипт),
светлая тема по умолчанию, пустые разделы скрыты, пользовательская структура сохраняется, печать. Без сети и браузера."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import re
from pathlib import Path

from app.services import glass_html as gh

ROOT = Path(__file__).resolve().parents[2]

HOSTILE = """# Протокол <script>alert(1)</script>

**Встреча:** <img src=x onerror=alert(1)>

## Решения

1. [нажми](javascript:alert(1)) и [ок](https://example.org/a) и [почта](mailto:a@example.org)
2. <iframe src="https://example.org"></iframe> `<b>код</b>`

## Задачи

| № | Задача | Ответственный |
|---|---|---|
| 1 | <b onmouseover=alert(1)>x</b> | Иван Очень-Длинное-Двойное-Имя-Фамилия-Отчество |
"""


def page_of(md: str, **kw) -> str:
    return gh.to_html(md, "Тест", **kw)


def test_server_css_copy_is_in_sync_with_the_frontend_file():
    spec = importlib.util.spec_from_file_location("sync_glassdoc", ROOT / "scripts" / "sync_glassdoc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DST.read_text(encoding="utf-8") == mod.render(), "glassdoc_css.py отстал: python3 scripts/sync_glassdoc.py"


def test_model_text_cannot_add_tags_scripts_or_dangerous_links():
    page = page_of(HOSTILE)
    body = page.split("<main", 1)[1].split("<script>")[0]            # без собственного скрипта в конце страницы
    assert "<script" not in body and "<img" not in body and "<iframe" not in body and "<b onmouseover" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body and "&lt;script&gt;alert(1)&lt;/script&gt;" in body, "теги показываются текстом, а не исполняются"
    assert "javascript:" not in body.split("href=")[0] and 'href="javascript' not in body, "ссылка javascript: не становится ссылкой"
    assert 'href="https://example.org/a"' in body and 'href="mailto:a@example.org"' in body
    assert "&lt;b&gt;код&lt;/b&gt;" in body, "код показывается как текст"


def test_page_has_one_own_script_pinned_by_hash_and_no_external_resources():
    page = page_of(HOSTILE)
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1 and page.count("<script") == 1
    digest = "sha256-" + base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert f"script-src '{digest}'" in page and f"script-src '{digest}'" in gh.CSP_HEADER
    assert "default-src 'none'" in gh.CSP and "connect-src" not in gh.CSP and "sandbox" in gh.CSP_HEADER
    head = page.split("<main", 1)[0]
    assert not re.search(r"(src|href)=\"https?:", head) and "@import" not in head and "url(http" not in head


def test_light_theme_is_the_default_regardless_of_system_dark_mode_and_print_is_plain():
    page = page_of("# T\n\n## Решения\n\n1. да\n")
    assert 'data-gd-theme="light"' in page and '<meta name="color-scheme" content="light">' in page
    css = gh.GLASSDOC_CSS
    assert "prefers-color-scheme" not in css, "тёмная тема только добровольная (кнопка), системная настройка её не включает"
    assert "@media print" in css and ".gd-noprint" in css


def test_empty_sections_are_not_shown_and_custom_template_structure_is_kept():
    page = page_of("# Протокол\n\n**Встреча:** Тест\n\n## Краткие итоги\n\nОбсудили.\n")
    assert "Решения" not in page and "Открытые вопросы" not in page
    med = "# Приём\n\n## Жалобы\n\nГоловная боль.\n\n## Анамнез\n\nБез особенностей.\n\n## Назначения\n\n1. Покой\n"
    out = page_of(med)
    assert out.index("Жалобы") < out.index("Анамнез") < out.index("Назначения"), "авторский порядок и названия разделов остаются"


def test_wide_tables_scroll_inside_their_own_wrapper_and_long_names_wrap():
    page = page_of(HOSTILE)
    assert 'class="gd-tablewrap"' in page
    css = gh.GLASSDOC_CSS
    assert "overflow-wrap" in css and "word-break" in css or "overflow-wrap:anywhere" in css.replace(" ", "")


def test_source_marks_become_chips_with_the_time_only():
    page = page_of("# T\n\n## Решения\n\n1. Обновить — _[10:05:00] Анна_\n")
    assert 'class="gd-src" data-ts="10:05:00"' in page


def test_standalone_map_file_uses_the_same_glass_styles_light_by_default_and_has_no_service_details():
    from app.services import map_export
    html = map_export.standalone_html("Карта", {"meeting": {}, "topics": []}, {"model": "секретная-модель", "llm_profile": "Шлюз", "retries": 34, "warnings": ["неполно"]}).decode()
    assert 'data-gd-theme="light"' in html and "Peregovorka Glass Documents" in html and "prefers-color-scheme" not in html
    assert "секретная-модель" not in html and "Шлюз" not in html and "34" not in html.split('id="pg-map-data"')[1].split("</script>")[0] and "неполно" in html
