#!/usr/bin/env bash
# bootstrap-apt-repo.sh — один раз: создаёт публичный репозиторий сайта APT (calleograph/peregovorka-apt) и включает GitHub Pages в режиме
# «из GitHub Actions» (сайт выкладывает только проверяющий workflow pages.yml, ветка gh-pages не нужна). Повторный запуск ничего не ломает.
#   scripts/linux-apt/bootstrap-apt-repo.sh
# Затем: выпуск приложения (cd desktop && npm run release -- X.Y.Z --push) и публикация репозитория (scripts/linux-apt/apt-publish.sh) — docs/LINUX.md.
set -euo pipefail
REPO="calleograph/peregovorka-apt"
if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "репозиторий $REPO уже есть"
else
  gh repo create "$REPO" --public --description "Репозиторий APT приложения Peregovorka для Linux (Ubuntu, Debian): подписанные индексы; сайт — GitHub Pages" \
    --homepage "https://calleograph.github.io/peregovorka-apt/"
  echo "создан $REPO"
fi
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null
  echo "GitHub Pages: режим «из GitHub Actions»"
else
  gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null
  echo "GitHub Pages включён (из GitHub Actions)"
fi
gh api "repos/$REPO/pages" --jq '"адрес сайта: \(.html_url)"'
