# shellcheck shell=bash
# Подключается из run.sh: канал обновления для установки по релизному тегу (отсоединённый HEAD) и запись истории без OLD/TARGET.
# Используются: ROOT, TMP, t, eq.

t "история: запись без OLD/TARGET под set -u не падает (раньше — unbound variable при остановке до выбора цели)" bash -c '
  set -uo pipefail
  source "$1/scripts/lib/common.sh" >/dev/null 2>&1
  d="$2/hist-nounset"; mkdir -p "$d/updater"
  DATA_ROOT="$d"; REPO_ROOT="$1"; DRY_RUN=0; unset OLD TARGET
  upd_history_append failed "Определение проекта"
  grep -q "\"result\":\"failed\"" "$d/updater/history.ndjson" && grep -q "\"stage\":\"Определение проекта\"" "$d/updater/history.ndjson" \
    && grep -q "\"to_version\":\"\"" "$d/updater/history.ndjson" && grep -q "\"to_commit\":\"\"" "$d/updater/history.ndjson"
' _ "$ROOT" "$TMP"

t "канал: отсоединённый HEAD → самый новый релизный тег vX.Y.Z (v0.10.0, не rc и не v0.9.0); ветка не переключается" bash -c '
  set -uo pipefail
  source "$1/scripts/lib/common.sh" >/dev/null 2>&1
  r="$2/relrepo"; rm -rf "$r"; mkdir -p "$r"; cd "$r" || exit 1
  git init -q; git config user.email t@t; git config user.name t
  for v in 0.9.0 0.10.0 0.10.1-rc1; do echo "$v" > VERSION; git add VERSION; git commit -qm "$v"; git tag "v$v"; done
  git checkout -q --detach v0.9.0 || exit 1
  REPO_ROOT="$r"
  [ "$(upd_release_tag)" = v0.10.0 ] || exit 1
  [ "$(upd_release_target)" = "$(git rev-parse "v0.10.0^{commit}")" ] || exit 1
  [ -z "$(git symbolic-ref -q HEAD)" ] || exit 1
  [ "$(upd_head_release_tag)" = v0.9.0 ] || exit 1
  [ "$(upd_check_commit)" = "$(git rev-parse "v0.10.0^{commit}")" ] || exit 1
' _ "$ROOT" "$TMP"

t "канал: ветка без upstream не сравнивается молча с тегом (upd_check_commit пуст)" bash -c '
  set -uo pipefail
  source "$1/scripts/lib/common.sh" >/dev/null 2>&1
  r="$2/relrepo"; cd "$r" || exit 1
  git checkout -q -B work v0.9.0 || exit 1
  REPO_ROOT="$r"
  [ -z "$(upd_check_commit)" ]
' _ "$ROOT" "$TMP"

t "канал: HEAD на произвольном коммите (после отката или установки по SHA) не считается релизом — цель не определяется" bash -c '
  set -uo pipefail
  source "$1/scripts/lib/common.sh" >/dev/null 2>&1
  r="$2/relrepo"; cd "$r" || exit 1
  git checkout -q work || exit 1
  echo x > extra.txt; git add extra.txt; git commit -qm extra; git checkout -q --detach || exit 1
  REPO_ROOT="$r"
  [ -z "$(upd_head_release_tag)" ] && [ -z "$(upd_check_commit)" ]
' _ "$ROOT" "$TMP"

t "канал: без релизных тегов upd_release_target пуст (update.sh остановится с понятным сообщением)" bash -c '
  set -uo pipefail
  source "$1/scripts/lib/common.sh" >/dev/null 2>&1
  r="$2/notags"; rm -rf "$r"; mkdir -p "$r"; cd "$r" || exit 1
  git init -q; git config user.email t@t; git config user.name t
  git commit -q --allow-empty -m x; git checkout -q --detach || exit 1
  REPO_ROOT="$r"
  [ -z "$(upd_release_target)" ]
' _ "$ROOT" "$TMP"

t "update.sh: при отсоединённом HEAD канал — релиз, переход через checkout --detach; merge --ff-only только для ветки" bash -c '
  f="$1/scripts/update.sh"
  grep -q "CHANNEL=release" "$f" && grep -q "CHANNEL=branch" "$f" && grep -q "upd_head_release_tag" "$f" && grep -q "checkout --detach \"\$TARGET\"" "$f" && grep -q "if \[ \"\$CHANNEL\" = branch \]" "$f"
' _ "$ROOT"

t "updater.sh и check-updates.sh сравнивают с той же целью (upd_check_commit), без жёсткого origin/main" bash -c '
  ! grep -q "echo origin/main" "$1/scripts/updater.sh" && grep -q "upd_check_commit" "$1/scripts/updater.sh" && grep -q "upd_check_commit" "$1/scripts/check-updates.sh"
' _ "$ROOT"

t "rollback.sh: после отката не советует переключаться на main (для релизной установки это неверно), различает тег и обычный commit" bash -c '
  ! grep -q "git switch main &&" "$1/scripts/rollback.sh" && grep -q "upd_head_release_tag" "$1/scripts/rollback.sh"
' _ "$ROOT"
