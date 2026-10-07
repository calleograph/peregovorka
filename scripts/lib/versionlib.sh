# shellcheck shell=bash
# Версии проекта: единый источник — файл VERSION (X.Y.Z); журнал изменений — CHANGELOG.md (заголовки «## X.Y.Z …»).
# Чистые функции без побочных эффектов: подключаются из release.sh, updater.sh, update.sh и тестов.

ver_valid() { [[ "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

# ver_gt A B — A строго новее B (числовое сравнение, а не строковое: 0.1.10 > 0.1.9)
ver_gt() {
  ver_valid "${1:-}" && ver_valid "${2:-}" || return 1
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]
}

# ver_bump ТЕКУЩАЯ patch|minor|major|X.Y.Z → следующая версия
ver_bump() {
  local cur="$1" kind="$2" a b c
  ver_valid "$cur" || return 1
  IFS=. read -r a b c <<<"$cur"
  case "$kind" in
    patch) echo "$a.$b.$((c + 1))" ;;
    minor) echo "$a.$((b + 1)).0" ;;
    major) echo "$((a + 1)).0.0" ;;
    *) ver_valid "$kind" && echo "$kind" || return 1 ;;
  esac
}

# Версии из CHANGELOG.md сверху вниз (свежая первой)
changelog_versions() { sed -n -E 's/^## +\[?([0-9]+\.[0-9]+\.[0-9]+)\]?.*/\1/p' "$1"; }
changelog_top_version() { changelog_versions "$1" | head -1; }
changelog_has_unreleased() { grep -qiE '^## +(unreleased|не выпущено)' "$1"; }

# Текст раздела версии (без заголовка, без пустых строк по краям)
changelog_section() { # файл версия
  awk -v v="$2" '
    /^## / { if (on) exit; on = ($0 ~ "^## +\\[?" v "\\]?([ ]|$)") ; next }
    on { print }' "$1" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}' | sed '/./,$!d'
}

# Раздел «Unreleased» (то, что накоплено после последнего выпуска)
changelog_unreleased() {
  awk '/^## / { if (on) exit; on = ($0 ~ /^## +([Uu]nreleased|[Нн]е выпущено)/); next } on { print }' "$1" | sed '/./,$!d'
}

# Разделы версий НОВЕЕ заданной (для «что изменилось» в окне обновления): заголовки сохраняются
changelog_since() { # файл версия_установленная
  local f="$1" cur="$2" v out="" body
  while read -r v; do
    [ -n "$v" ] || continue
    ver_gt "$v" "$cur" || break
    body="$(changelog_section "$f" "$v")"
    out+="## ${v}"$'\n'"${body}"$'\n\n'
  done < <(changelog_versions "$f")
  printf '%s' "$out"
}

# Версия из содержимого VERSION (пробелы и перевод строки отбрасываются)
version_file_read() { tr -d '[:space:]' < "$1" 2>/dev/null; }

# Короткий commit HEAD: устойчиво к «dubious ownership» (репозиторий принадлежит другому пользователю, например при запуске исполнителя от root)
# и к отсутствию git (читает .git встроенными средствами bash). Пустой вывод — не удалось определить. Код возврата всегда 0 (безопасно под set -e).
git_head_commit() { # каталог_репозитория → 12 символов
  local root="$1" c="" head="" ref="" sha="" name=""
  if command -v git >/dev/null 2>&1; then
    c="$(git -c "safe.directory=$root" -C "$root" rev-parse HEAD 2>/dev/null)"; c="${c%%[!0-9a-f]*}"
  fi
  if ! [[ "$c" =~ ^[0-9a-f]{40}$ ]] && [ -f "$root/.git/HEAD" ]; then
    c=""
    read -r head < "$root/.git/HEAD" 2>/dev/null || true
    head="${head%$''}"
    if [[ "$head" =~ ^ref:\ (.+)$ ]]; then
      ref="${BASH_REMATCH[1]}"
      if [ -f "$root/.git/$ref" ]; then read -r c < "$root/.git/$ref" 2>/dev/null || true
      elif [ -f "$root/.git/packed-refs" ]; then
        while read -r sha name; do [ "$name" = "$ref" ] && { c="$sha"; break; }; done < "$root/.git/packed-refs"
      fi
    else c="$head"; fi
    c="${c%$''}"
  fi
  if [[ "$c" =~ ^[0-9a-f]{40}$ ]]; then printf '%s' "${c:0:12}"; fi
  return 0
}
