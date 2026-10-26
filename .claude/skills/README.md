# Навыки (skills) проекта

Claude Code находит их сам по полю `description` в `SKILL.md`; правила выбора — в [CLAUDE.md](../../CLAUDE.md).

## Свои

| Навык | Когда |
| --- | --- |
| `release` | выпуск сервера: чек-лист VERSION → CHANGELOG → HISTORY → PROJECT → README → docs → CI |
| `desktop-dev` | любая правка `desktop/`: команды dev / preview / release этого проекта, проверки, диагностика запуска |
| `electron-windows-client` | общие решения и ловушки Electron под Windows: оболочка `app://`, первый запуск, CDP-проверки, реестр, NSIS/WiX, подпись, обновления |
| `smart-autofocus` | любая новая или изменяемая форма, диалог, плавающее окно, панель поиска: правило «курсор сразу в первое нужное поле», когда нельзя, как проверять (универсально, не только для этого проекта) |
| `github-actions-ci` | запуск, ожидание и разбор CI через REST API без `gh`, разделение быстрых и длинных проверок, Windows-раннеры |

## Сторонние

Установлены как есть, без изменений; только текст (сценариев и сетевых вызовов нет — проверено перед установкой).

| Навык | Источник | Версия (commit) | Лицензия |
| --- | --- | --- | --- |
| `electron-development` | https://github.com/Mindrally/skills | 7682ca77710e0971eab4e0ae5dddfa281aea0ba5 | Apache-2.0 (`LICENSE` рядом) |
| `debugging-wizard` | https://github.com/Jeffallan/claude-skills | 02b05028ba6c93f7aed45314b15c0dd5abead9d6 | MIT (`LICENSE` рядом) |

`playwright-expert` (Jeffallan/claude-skills) удалён: Playwright `_electron` несовместим с закрытым предохранителем `--inspect` клиента,
а живые сценарии комнаты (`tests/live/`) работают без Playwright.

## Пополнение

Нетривиальная ошибка, которая может повториться, — одна строка «симптом → причина → как предотвращено» в «Ловушки» профильного навыка.
Не журнал работ (он — в HISTORY.md), без частных данных (только `example.org`, `192.0.2.x`). Новый навык — только для новой области, а не для одного случая.
