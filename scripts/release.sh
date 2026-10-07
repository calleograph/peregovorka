#!/usr/bin/env bash
# Выпуск новой версии проекта. Версия меняется в ОДНОМ месте — файл VERSION; всё остальное берётся из него:
#   • образы backend/asr/web собираются с APP_VERSION из VERSION (scripts/lib/common.sh → host_version_info), commit — из git;
#   • интерфейс, «Обновления и версии», /api/v1/version, /version.json показывают «версия · commit»;
#   • git-тег vX.Y.Z и GitHub Release создаются из той же версии (.github/workflows/release.yml берёт описание из CHANGELOG.md).
#
# Порядок работы разработчика:
#   1. по ходу работы дописывать заметные изменения в раздел «## Unreleased» файла CHANGELOG.md (по-человечески, без деталей рефакторинга);
#   2. ./scripts/release.sh patch          # 0.1.4 → 0.1.5 (minor — 0.2.0, либо явная версия: ./scripts/release.sh 0.2.0)
#      скрипт: проверяет чистоту дерева, переименовывает «Unreleased» в «## X.Y.Z (дата)», пишет VERSION и frontend/package.json,
#      делает commit «Релиз X.Y.Z» и аннотированный тег vX.Y.Z (описание тега = раздел CHANGELOG);
#   3. ./scripts/release.sh push           # git push origin <ветка> vX.Y.Z → GitHub Actions публикует релиз.
#
# Прочее: ./scripts/release.sh check      — проверка согласованности (VERSION, CHANGELOG, package.json, тег);
#         ./scripts/release.sh notes [X.Y.Z] — текст раздела CHANGELOG (его же использует workflow релиза).
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=lib/versionlib.sh
source "$REPO_ROOT/scripts/lib/versionlib.sh"
VF="$REPO_ROOT/VERSION"; CL="$REPO_ROOT/CHANGELOG.md"; PJ="$REPO_ROOT/frontend/package.json"; PL="$REPO_ROOT/frontend/package-lock.json"

die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
g() { git -c "safe.directory=$REPO_ROOT" -C "$REPO_ROOT" "$@"; }

pkg_version() { sed -n -E 's/^  "version": *"([^"]+)".*/\1/p' "$PJ" | head -1; }
set_pkg_version() { # файл версия: меняет версию пакета (строка с отступом в 2 пробела)
  local f="$1" v="$2"
  [ -f "$f" ] || return 0
  sed -i -E "0,/^  \"version\": *\"[^\"]+\"/s//  \"version\": \"$v\"/" "$f"
}
set_lock_version() { # package-lock.json: версия в корне и в packages[""] (первое поле "version" после `"": {`)
  local f="$1" v="$2" tmp
  [ -f "$f" ] || return 0
  tmp="$(mktemp)"
  awk -v v="$v" '
    !a && /^  "version":/ { sub(/"version": *"[^"]*"/, "\"version\": \"" v "\""); a = 1 }
    /^    "": *\{/ { inroot = 1 }
    inroot && !b && /^      "version":/ { sub(/"version": *"[^"]*"/, "\"version\": \"" v "\""); b = 1 }
    { print }' "$f" > "$tmp" && cat "$tmp" > "$f"; rm -f "$tmp"
}

cmd_check() {
  local v rc=0 top tag
  [ -f "$VF" ] || die "нет файла VERSION"
  v="$(version_file_read "$VF")"
  ver_valid "$v" || { say "✗ VERSION: «$v» — не вида X.Y.Z"; exit 1; }
  say "VERSION: $v"
  [ -f "$CL" ] || { say "✗ нет CHANGELOG.md"; exit 1; }
  top="$(changelog_top_version "$CL")"
  if [ "$top" = "$v" ]; then say "✓ CHANGELOG: верхний раздел — $v"
  else say "✗ CHANGELOG: верхний раздел «${top:-нет}», а VERSION $v (добавьте раздел ## $v или выпустите версию: ./scripts/release.sh patch)"; rc=1; fi
  [ -n "$(changelog_section "$CL" "$v")" ] || { say "✗ CHANGELOG: раздел $v пуст"; rc=1; }
  if [ -f "$PJ" ]; then
    [ "$(pkg_version)" = "$v" ] && say "✓ frontend/package.json: $v" || { say "✗ frontend/package.json: $(pkg_version), а VERSION $v"; rc=1; }
  fi
  tag="$(g tag --points-at HEAD 2>/dev/null | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
  if [ -n "$tag" ]; then [ "$tag" = "v$v" ] && say "✓ тег на HEAD: $tag" || { say "✗ тег на HEAD $tag, а VERSION $v"; rc=1; }; fi
  exit "$rc"
}

cmd_notes() {
  local v="${1:-$(version_file_read "$VF")}"
  changelog_section "$CL" "$v"
}

cmd_release() {
  local kind="$1" cur new today body branch
  cur="$(version_file_read "$VF")"; ver_valid "$cur" || die "VERSION повреждён: «$cur»"
  new="$(ver_bump "$cur" "$kind")" || die "неизвестный вид версии «$kind» (patch | minor | major | X.Y.Z)"
  ver_gt "$new" "$cur" || die "новая версия $new должна быть больше текущей $cur"
  g rev-parse --git-dir >/dev/null 2>&1 || die "это не git-репозиторий"
  [ -z "$(g status --porcelain)" ] || die "в рабочем дереве есть несохранённые изменения — сначала закоммитьте их"
  g rev-parse -q --verify "refs/tags/v$new" >/dev/null && die "тег v$new уже существует"
  branch="$(g rev-parse --abbrev-ref HEAD)"
  [ -f "$CL" ] || die "нет CHANGELOG.md"
  today="$(date +%Y-%m-%d)"
  if [ "$(changelog_top_version "$CL")" = "$new" ] && ! changelog_unreleased "$CL" | grep -q .; then
    :  # раздел новой версии уже написан вручную, накопленного в Unreleased нет
  elif changelog_has_unreleased "$CL"; then
    [ -n "$(changelog_unreleased "$CL")" ] || die "раздел Unreleased в CHANGELOG.md пуст: опишите заметные изменения (или одну строку «Исправление отдельных ошибок.»)"
    # накопленное попадает в раздел новой версии, а пустой «Unreleased» остаётся сверху для следующей
    local tmp; tmp="$(mktemp)"
    awk -v h="## $new ($today)" '/^## +([Uu]nreleased|[Нн]е выпущено)/ && !d { print; print ""; print h; d = 1; next } { print }' "$CL" > "$tmp" && cat "$tmp" > "$CL"; rm -f "$tmp"
  else
    die "в CHANGELOG.md нет раздела «## Unreleased» и нет раздела $new — опишите изменения этой версии"
  fi
  body="$(changelog_section "$CL" "$new")"
  [ -n "$body" ] || die "раздел $new в CHANGELOG.md пуст"
  printf '%s\n' "$new" > "$VF"
  set_pkg_version "$PJ" "$new"
  set_lock_version "$PL" "$new"
  g add VERSION CHANGELOG.md frontend/package.json frontend/package-lock.json 2>/dev/null
  g commit -q -m "Релиз $new" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" || die "commit не удался"
  g tag -a "v$new" -m "Peregovorka $new"$'\n\n'"$body" || die "не удалось создать тег"
  say "Готово: $cur → $new, commit «Релиз $new», тег v$new."
  say "Дальше: ./scripts/release.sh push   (отправит ветку $branch и тег; GitHub Actions опубликует релиз)"
}

cmd_push() {
  local v tag
  v="$(version_file_read "$VF")"; tag="v$v"
  g rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "тега $tag нет — сначала ./scripts/release.sh patch"
  g push origin HEAD "$tag" || die "push не удался"
  say "Отправлено: ветка и тег $tag. Релиз появится на GitHub после выполнения workflow «Release»."
}

case "${1:-}" in
  check) cmd_check ;;
  notes) cmd_notes "${2:-}" ;;
  push) cmd_push ;;
  patch|minor|major) cmd_release "$1" ;;
  "" | -h | --help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) ver_valid "$1" && cmd_release "$1" || die "неизвестная команда «$1» (patch | minor | major | X.Y.Z | check | notes | push)" ;;
esac
