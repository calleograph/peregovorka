---
name: desktop-dev
description: Быстрый цикл разработки приложения Peregovorka для Windows (desktop/) — dev, preview, release, проверки первого запуска и вёрстки, диагностика запуска. Использовать при любой правке desktop/, перед выпуском клиента и когда «окно пустое / not found / не запускается».
---

# Приложение Windows: как работать быстро

Команды и устройство этого проекта. Общие решения и ловушки Electron под Windows — навык `electron-windows-client`; CI — `github-actions-ci`.
Подробно — [docs/DESKTOP_DEV.md](../../../docs/DESKTOP_DEV.md). Порядок, без пропусков:

1. **Правка интерфейса или запуска** — `cd desktop && npm run dev` (окно перезагружается само; журнал запуска в терминале). Для поведения «у нового пользователя» — `npm run dev -- --fresh`.
2. **Сразу после правки** — `npm run check -- firstrun --app dev` (первый запуск, ошибки консоли и журнала) и при правке CSS — `npm run check -- shots --app dev`, затем смотреть `out/check/*.png` глазами.
3. **Перед коммитом** — `npm run typecheck && npm test && node scripts/security-check.mjs`, затем `npm run preview` (собранная программа без установщика + все проверки). Под Linux проверки — под X-сервером (`DISPLAY=:99`, Xvfb), переменная `ELECTRON_RUN_AS_NODE` снимается сценариями сами.
4. **Коммит в `feat/**`** — CI запускает только checks + preview на Windows (≈ 1,5 мин), не серверные тесты и не установщики. Ждать зелёного `preview`.
5. **Выпуск** — только по просьбе владельца: `npm run release -- X.Y.Z --push` с `main` (раздел `## X.Y.Z` в `desktop/CHANGELOG.md` обязателен). EXE/MSI собирает CI.

Правила:
* Не называть «проверено на Windows» то, что прошло только под Linux; путь `app://` и файлы — проверяет preview на Windows (CI или `npm run preview` на Windows).
* «Пустое окно / not found» — сначала `Peregovorka.exe --debug-log` и `--diagnostics=<файл>` (раздел `shell`), журнал `%APPDATA%\Peregovorka\logs\main.log` (`shell.not_found`, `preload.error`, `shell.console`).
* Новая команда оболочки (`call("shell:…")`) — добавить в `src/preload/shell.ts` и обработчик в `main.ts` (тест это проверяет).
* Полный контур (EXE/MSI, установка, обновление, агент подписи) на ветке — ручным запуском workflow `desktop.yml` (`workflow_dispatch`).
* Ловушка: `npm run release`/`npm run preview` под Linux без `DISPLAY` → «окно app://shell/index.html не появилось за 60 с» (Electron без экрана выходит с кодом 1) → запускать с `DISPLAY=<экран Xvfb>`.
* Ловушка: шаг CI «EXE: обновление из приложения» на теге `desktop-v*` пропускается (нет ключа обновлений) — зелёный выпуск не доказывает обновление из приложения; смотреть прогон `main`/`workflow_dispatch`.
