#!/usr/bin/env bash
# publish-wiki.sh — копирует исходники вики (wiki/*.md, wiki/images) в репозиторий вики GitHub (<репозиторий>.wiki.git).
#   scripts/publish-wiki.sh [--yes] [--remote URL]
# Без --yes только показывает перечень файлов и сводку изменений (ничего не отправляет). Принудительная перезапись истории не выполняется:
# при расхождении история вики не трогается, скрипт останавливается. Служебный каталог wiki/_meta не публикуется.
# Репозиторий вики появляется после сохранения первой страницы в веб-интерфейсе (вкладка Wiki).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YES=0; REMOTE="${WIKI_REMOTE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1; shift ;;
    --remote) REMOTE="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$REMOTE" ]; then
  origin="$(git -C "$ROOT" remote get-url origin)"; REMOTE="${origin%.git}.wiki.git"
fi
python3 "$ROOT/scripts/check_wiki.py" || { echo "Проверка вики не пройдена: публикация отменена" >&2; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
git clone --quiet "$REMOTE" "$TMP/wiki" 2>/dev/null || { echo "Репозиторий вики недоступен: $REMOTE. Сохраните первую страницу Home в веб-интерфейсе (вкладка Wiki) и повторите." >&2; exit 1; }
# синхронизация: только .md и images; служебное и _meta не копируются; лишние страницы в вики удаляются как изменение, видимое в сводке
find "$TMP/wiki" -maxdepth 1 -name '*.md' -delete
cp "$ROOT"/wiki/*.md "$TMP/wiki/"
if [ -d "$ROOT/wiki/images" ]; then rm -rf "$TMP/wiki/images"; cp -r "$ROOT/wiki/images" "$TMP/wiki/images"; fi
git -C "$TMP/wiki" add -A
echo "=== изменения в вики ($REMOTE) ==="
git -C "$TMP/wiki" status --short
git -C "$TMP/wiki" diff --cached --stat | tail -3
if [ -z "$(git -C "$TMP/wiki" status --porcelain)" ]; then echo "Изменений нет."; exit 0; fi
if [ "$YES" -ne 1 ]; then echo "Это только предпросмотр. Для отправки: scripts/publish-wiki.sh --yes"; exit 0; fi
V="$(cat "$ROOT/VERSION")"
# Личность берём из основного репозитория: во временном клоне своей нет.
git -C "$TMP/wiki" -c user.name="$(git config user.name || echo peregovorka)" -c user.email="$(git config user.email || echo noreply@example.org)" commit --quiet -m "Вики: версия $V"
git -C "$TMP/wiki" push --quiet origin HEAD && echo "Опубликовано."
