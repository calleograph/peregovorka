# shellcheck shell=bash
# Подключается из run.sh после part_fixes.sh: версионирование (VERSION, CHANGELOG, release.sh, versionlib, версии в remote.json).
# Все действия с git — только во временных репозиториях ($TMP); release.sh получает REPO_ROOT явно.
VL="$ROOT/scripts/lib/versionlib.sh"

# ---- чистые функции
t "ver_gt: сравнение числовое, а не строковое (0.1.10 > 0.1.9)" bash -c 'source "$1"; ver_gt 0.1.10 0.1.9 && ver_gt 0.2.0 0.1.99 && ! ver_gt 0.1.4 0.1.4 && ! ver_gt 0.1.3 0.1.4 && ! ver_gt x 0.1.0' _ "$VL"
t "ver_bump: patch/minor/major/явная версия, мусор отвергается" bash -c 'source "$1"; [ "$(ver_bump 0.1.4 patch)" = 0.1.5 ] && [ "$(ver_bump 0.1.9 patch)" = 0.1.10 ] && [ "$(ver_bump 0.1.4 minor)" = 0.2.0 ] && [ "$(ver_bump 0.1.4 major)" = 1.0.0 ] && [ "$(ver_bump 0.1.4 0.3.1)" = 0.3.1 ] && ! ver_bump 0.1.4 foo >/dev/null && ! ver_bump bad patch >/dev/null' _ "$VL"
CLS="$TMP/cl-sample.md"
cat > "$CLS" <<'EOF'
# Changelog

## Unreleased
- Ещё не выпущено.

## 0.1.4 (2026-10-07)
- Четвёртое.
- Ещё.

## 0.1.3 (2026-10-06)
- Третье.

## 0.1.2
- Второе.
EOF
t "changelog: версии по порядку, верхняя — 0.1.4 (Unreleased не считается)" bash -c 'source "$1"; [ "$(changelog_versions "$2" | tr "\n" " ")" = "0.1.4 0.1.3 0.1.2 " ] && [ "$(changelog_top_version "$2")" = 0.1.4 ]' _ "$VL" "$CLS"
t "changelog_section: только текст раздела, без заголовка и пустых краёв" bash -c 'source "$1"; [ "$(changelog_section "$2" 0.1.4)" = "$(printf -- "- Четвёртое.\n- Ещё.")" ] && [ "$(changelog_section "$2" 0.1.2)" = "- Второе." ] && [ -z "$(changelog_section "$2" 9.9.9)" ]' _ "$VL" "$CLS"
t "changelog_unreleased: накопленное после последнего выпуска" bash -c 'source "$1"; [ "$(changelog_unreleased "$2")" = "- Ещё не выпущено." ] && changelog_has_unreleased "$2"' _ "$VL" "$CLS"
t "changelog_since: разделы новее установленной версии, старые не попадают" bash -c 'source "$1"; o="$(changelog_since "$2" 0.1.2)"; grep -q "^## 0.1.4" <<<"$o" && grep -q "Третье" <<<"$o" && ! grep -q "Второе" <<<"$o" && [ -z "$(changelog_since "$2" 0.1.4)" ]' _ "$VL" "$CLS"

# ---- commit без git-команды и при «dubious ownership»
GC="$TMP/gc"; rm -rf "$GC"; mkdir -p "$GC"; ( cd "$GC" || exit 1; git init -q . && git config user.email t@t && git config user.name t && echo a > a && git add -A && git commit -qm one && git branch -M main )
GH="$(git -C "$GC" rev-parse HEAD)"
t "git_head_commit: 12 символов HEAD" bash -c 'source "$1"; [ "$(git_head_commit "$2")" = "${3:0:12}" ]' _ "$VL" "$GC" "$GH"
t "git_head_commit: без git в PATH читает .git/HEAD и ссылку (commit не «unknown»)" bash -c 'source "$1"; PATH=/nonexistent; [ "$(git_head_commit "$2")" = "${3:0:12}" ]' _ "$VL" "$GC" "$GH"
t "git_head_commit: читает packed-refs" bash -c 'source "$1"; ( cd "$2" && git pack-refs --all ) && PATH=/nonexistent && [ "$(git_head_commit "$2")" = "${3:0:12}" ]' _ "$VL" "$GC" "$GH"
t "git_head_commit: не репозиторий — пусто, без ошибки" bash -c 'source "$1"; mkdir -p "$2"; [ -z "$(git_head_commit "$2")" ]' _ "$VL" "$TMP/not-a-repo"
t "host_version_info: VERSION и commit из репозитория (формат X.Y.Z, 12 символов, не unknown)" bash -c 'mkdir -p "$2/scripts/lib"; cp "$3"/scripts/lib/*.sh "$2/scripts/lib/"; echo 0.7.3 > "$2/VERSION"; source "$2/scripts/lib/common.sh" >/dev/null 2>&1; REPO_ROOT="$2"; host_version_info; [ "$APP_VERSION" = 0.7.3 ] && [ "${#APP_GIT_COMMIT}" = 12 ] && [ "$APP_GIT_COMMIT" != unknown ]' _ x "$GC" "$ROOT"

# ---- release.sh во временном репозитории
mkrel() { # временный репозиторий с VERSION 0.1.4, CHANGELOG и frontend/package.json
  local d="$TMP/rel"; rm -rf "$d"; mkdir -p "$d/scripts/lib" "$d/frontend"
  cp "$ROOT/scripts/release.sh" "$d/scripts/"; cp "$ROOT/scripts/lib/versionlib.sh" "$d/scripts/lib/"
  echo 0.1.4 > "$d/VERSION"
  printf '# Changelog\n\n## Unreleased\n- Новая возможность для пользователей.\n\n## 0.1.4 (2026-10-07)\n- Прежнее.\n' > "$d/CHANGELOG.md"
  printf '{\n  "name": "x",\n  "version": "0.1.4",\n  "dependencies": {\n    "a": "1.0.0"\n  }\n}\n' > "$d/frontend/package.json"
  printf '{\n  "name": "x",\n  "version": "0.1.4",\n  "lockfileVersion": 3,\n  "packages": {\n    "": {\n      "name": "x",\n      "version": "0.1.4",\n      "dependencies": {}\n    },\n    "node_modules/a": {\n      "version": "1.0.0"\n    }\n  }\n}\n' > "$d/frontend/package-lock.json"
  ( cd "$d" || exit 1; git init -q . && git config user.email t@t && git config user.name t && git add -A && git commit -qm init && git branch -M main )
}
REL() { REPO_ROOT="$TMP/rel" bash "$TMP/rel/scripts/release.sh" "$@"; }
mkrel
t "release check: VERSION, CHANGELOG и package.json согласованы" bash -c 'REPO_ROOT="$1" bash "$1/scripts/release.sh" check' _ "$TMP/rel"
t "release patch: чужая рабочая копия с изменениями отвергается" bash -c 'echo x >> "$1/VERSION.tmp"; ! REPO_ROOT="$1" bash "$1/scripts/release.sh" patch >/dev/null 2>&1; r=$?; rm -f "$1/VERSION.tmp"; exit $r' _ "$TMP/rel"
t "release patch: 0.1.4 → 0.1.5, Unreleased перенесён в раздел версии, package.json и lock обновлены" bash -c 'REPO_ROOT="$1" bash "$1/scripts/release.sh" patch >/dev/null 2>&1 \
   && [ "$(tr -d "[:space:]" < "$1/VERSION")" = 0.1.5 ] && grep -q "^## 0.1.5 (" "$1/CHANGELOG.md" && grep -q "^## Unreleased" "$1/CHANGELOG.md" \
   && [ "$(sed -n -E "s/^  \"version\": *\"([^\"]+)\".*/\1/p" "$1/frontend/package.json")" = 0.1.5 ] \
   && [ "$(grep -c "\"version\": \"0.1.5\"" "$1/frontend/package-lock.json")" = 2 ] && grep -q "\"a\"" "$1/frontend/package.json" && grep -q "\"version\": \"1.0.0\"" "$1/frontend/package-lock.json"' _ "$TMP/rel"
t "release patch: commit «Релиз 0.1.5» и аннотированный тег v0.1.5 с описанием из CHANGELOG" bash -c 'git -C "$1" log -1 --format=%s | grep -q "^Релиз 0.1.5$" && [ "$(git -C "$1" cat-file -t v0.1.5)" = tag ] && git -C "$1" tag -l --format="%(contents)" v0.1.5 | grep -q "Новая возможность"' _ "$TMP/rel"
t "release check после выпуска: тег на HEAD совпадает с VERSION" bash -c 'REPO_ROOT="$1" bash "$1/scripts/release.sh" check | grep -q "тег на HEAD: v0.1.5"' _ "$TMP/rel"
t "release notes: раздел версии для описания GitHub-релиза" bash -c '[ "$(REPO_ROOT="$1" bash "$1/scripts/release.sh" notes 0.1.5)" = "- Новая возможность для пользователей." ]' _ "$TMP/rel"
t "release patch: пустой Unreleased отвергается с понятным сообщением" bash -c 'REPO_ROOT="$1" bash "$1/scripts/release.sh" patch 2>&1 | grep -q "Unreleased" ; [ "$(tr -d "[:space:]" < "$1/VERSION")" = 0.1.5 ]' _ "$TMP/rel"
t "release X.Y.Z: версия не больше текущей отвергается; повторный тег невозможен" bash -c '! REPO_ROOT="$1" bash "$1/scripts/release.sh" 0.1.0 >/dev/null 2>&1 && ! REPO_ROOT="$1" bash "$1/scripts/release.sh" 0.1.5 >/dev/null 2>&1' _ "$TMP/rel"
t "release check: рассинхрон VERSION и CHANGELOG/package.json обнаруживается" bash -c 'echo 0.9.0 > "$1/VERSION"; ! REPO_ROOT="$1" bash "$1/scripts/release.sh" check >/dev/null 2>&1; r=$?; echo 0.1.5 > "$1/VERSION"; exit $r' _ "$TMP/rel"
mkrel
t "release minor с готовым разделом новой версии (без Unreleased) и явная версия" bash -c 'printf "# Changelog\n\n## 0.2.0 (2026-10-08)\n- Крупный этап.\n\n## 0.1.4 (2026-10-07)\n- Прежнее.\n" > "$1/CHANGELOG.md"; git -C "$1" commit -qam ch; REPO_ROOT="$1" bash "$1/scripts/release.sh" 0.2.0 >/dev/null 2>&1 && [ "$(tr -d "[:space:]" < "$1/VERSION")" = 0.2.0 ] && git -C "$1" rev-parse -q --verify refs/tags/v0.2.0 >/dev/null' _ "$TMP/rel"

# ---- в самом репозитории проекта
t "проект: VERSION — X.Y.Z, верхний раздел CHANGELOG и frontend/package.json совпадают" bash -c 'REPO_ROOT="$1" bash "$1/scripts/release.sh" check' _ "$ROOT"
t "проект: .env.example не задаёт версию и commit (источник — VERSION и git)" bash -c '! grep -qE "^APP_(VERSION|GIT_COMMIT)=" "$1/.env.example"' _ "$ROOT"
t "проект: workflow релиза берёт описание из CHANGELOG и проверяет тег = VERSION" bash -c 'grep -q "release.sh notes" "$1/.github/workflows/release.yml" && grep -q "release.sh check" "$1/.github/workflows/release.yml" && grep -q "release.sh check" "$1/.github/workflows/ci.yml"' _ "$ROOT"

# ---- исполнитель обновлений: версии и «что нового» в remote.json
if [ -n "${PYJ:-}" ]; then
  mkorigin
  ( cd "$TMP/seed" || exit 1
    printf '# Changelog\n\n## 0.1.0\n- Первое.\n' > CHANGELOG.md; git add -A; git commit -qm "changelog"; git push -q origin main 2>/dev/null )
  git -C "$TMP/cl" pull -q --ff-only 2>/dev/null
  ( cd "$TMP/seed" || exit 1
    echo 0.1.1 > VERSION; printf '# Changelog\n\n## 0.1.1\n- Добавлен "чат" и \\ слэш.\n- Исправление отдельных ошибок.\n\n## 0.1.0\n- Первое.\n' > CHANGELOG.md
    git add -A; git commit -qm "Релиз 0.1.1"; git push -q origin main 2>/dev/null )
  rm -rf "$TMP/upd-data"; mkdir -p "$TMP/upd-data"
  t "check: в remote.json версии установленной и опубликованной редакции" bash -c '"$1/cl/scripts/updater.sh" check --env "$1/cl/.env" >/dev/null 2>&1 && [ "$(jget "$2/remote.json" "d[\"current_version\"]")" = 0.1.0 ] && [ "$(jget "$2/remote.json" "d[\"remote_version\"]")" = 0.1.1 ]' _ "$TMP" "$TMP/upd-data/updater"
  t "check: «что нового» — только разделы новее установленной версии; кавычки и слэши не ломают JSON" bash -c 'c="$(jget "$1/remote.json" "d[\"changelog\"]")"; grep -q "^## 0.1.1" <<<"$c" && grep -q "Добавлен \"чат\" и \\\\ слэш" <<<"$c" && ! grep -q "Первое" <<<"$c"' _ "$TMP/upd-data/updater"
fi
