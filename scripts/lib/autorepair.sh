# shellcheck shell=bash
# Автопочинка обновления: всё, из-за чего update.sh раньше останавливался и ждал человека, исправляется само — без потери чего-либо.
# Правило: ничего не удаляется безвозвратно. Локальные правки, новые файлы и локальные коммиты СНАЧАЛА сохраняются в
# $DATA_ROOT/backups/local-changes-<время>/ (и в git), и только потом рабочая копия приводится к целевой версии.
# Подключается из common.sh (нужны REPO_ROOT, DATA_ROOT, log/ok/warn/info, upd_* из updatelib.sh).

# Git под другим пользователем, чем владелец каталога (помощник обновлений, sudo): «detected dubious ownership» останавливал любую команду git.
# Разрешаем ТОЛЬКО этот каталог и ТОЛЬКО для процесса обновления (переменными окружения, глобальный git-конфиг не меняется).
upd_git_env() {
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$REPO_ROOT"
  export GIT_TERMINAL_PROMPT=0                       # не ждать ввода пароля от удалённого репозитория
}

# Зависшие блокировки git (процесс git был убит: перезагрузка, нехватка памяти, прерванное обновление) — index.lock и *.lock в refs.
# Снимаются только если ни один процесс git сейчас не работает в этом каталоге и файлу блокировки больше минуты.
upd_git_unlock() {
  local gd f n=0
  gd="$(git -C "$REPO_ROOT" rev-parse --absolute-git-dir 2>/dev/null)" || return 0
  pgrep -x git >/dev/null 2>&1 && return 0           # какой-то git сейчас работает — блокировка может быть живой
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    rm -f -- "$f" && n=$((n + 1))
  done < <(find "$gd" -maxdepth 4 -name '*.lock' -type f -mmin +1 2>/dev/null)
  [ "$n" -gt 0 ] && warn "Автопочинка: сняты зависшие блокировки git ($n) — прежний процесс git был прерван"
  return 0
}

# git fetch с починкой: обычные повторы (upd_git_fetch), затем принудительное обновление тегов (тег переставили в origin) и
# дозагрузка мелкого клона (shallow), затем повторное снятие блокировок. Нет сети — это не чинится: ошибка с понятной причиной.
upd_git_fetch_repair() {
  upd_git_fetch >/dev/null 2>&1 && return 0
  upd_git_unlock
  warn "Автопочинка: git fetch не прошёл — повтор с принудительным обновлением тегов"
  git -C "$REPO_ROOT" fetch --tags --force --prune origin >/dev/null 2>&1 && return 0
  if [ "$(git -C "$REPO_ROOT" rev-parse --is-shallow-repository 2>/dev/null)" = true ]; then
    warn "Автопочинка: мелкий клон — догружаю историю (git fetch --unshallow)"
    git -C "$REPO_ROOT" fetch --unshallow --tags --force origin >/dev/null 2>&1 && return 0
  fi
  GIT_RETRIES=2 upd_git_fetch >/dev/null 2>&1
}

upd_saved_dir() { # новый каталог для сохранения локальных изменений
  local d; d="${BACKUP_DIR:-$DATA_ROOT/backups}/local-changes-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$d" 2>/dev/null || { d="$DATA_ROOT/state/local-changes-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$d"; }
  chmod 700 "$d" 2>/dev/null
  printf '%s\n' "$d"
}

# Локальные правки файлов проекта и новые файлы (раньше: «Обнаружены локальные изменения. Обновление остановлено»).
# Сохраняются: changes.patch (git diff --binary HEAD), untracked.tar.gz (новые файлы, кроме игнорируемых — .env и данные не трогаются),
# status.txt и README.txt с командами возврата; дополнительно — git stash. Затем рабочая копия чистая. Печатает каталог сохранения.
upd_save_local_changes() {
  local st d untracked f
  st="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all 2>/dev/null)"
  [ -n "$st" ] || return 0
  d="$(upd_saved_dir)"
  printf '%s\n' "$st" > "$d/status.txt"
  git -C "$REPO_ROOT" diff --binary HEAD > "$d/changes.patch" 2>/dev/null
  untracked="$(git -C "$REPO_ROOT" ls-files --others --exclude-standard -z 2>/dev/null | tr '\0' '\n')"
  if [ -n "$untracked" ]; then
    git -C "$REPO_ROOT" ls-files --others --exclude-standard -z | tar -C "$REPO_ROOT" --null -T - -czf "$d/untracked.tar.gz" 2>/dev/null
  fi
  cat > "$d/README.txt" <<EOF
Локальные изменения файлов проекта, найденные перед обновлением $(date -Is) на commit $(git -C "$REPO_ROOT" rev-parse --short=12 HEAD).
Обновление сохранило их здесь и продолжилось на чистой копии. Ничего не удалено.
  status.txt          — что было изменено (git status)
  changes.patch       — правки отслеживаемых файлов: вернуть — git -C $REPO_ROOT apply --3way $d/changes.patch
  untracked.tar.gz    — новые файлы: вернуть — tar -C $REPO_ROOT -xzf $d/untracked.tar.gz
Та же копия есть в git stash (git -C $REPO_ROOT stash list). Настройки установки хранятся в .env и не входят сюда — они не менялись.
EOF
  if git -C "$REPO_ROOT" stash push --include-untracked -m "peregovorka-update-$(date +%Y%m%d-%H%M%S)" >/dev/null 2>&1 \
     && [ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all 2>/dev/null)" ]; then :
  else
    # stash не справился (права, необычные файлы): копия уже в каталоге — возвращаем отслеживаемые файлы, новые переносим туда же
    git -C "$REPO_ROOT" checkout -- . >/dev/null 2>&1
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      mkdir -p "$d/untracked/$(dirname "$f")" && mv -f -- "$REPO_ROOT/$f" "$d/untracked/$f" 2>/dev/null
    done <<<"$untracked"
  fi
  printf '%s\n' "$d"
  [ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all 2>/dev/null)" ]
}

# Локальные коммиты, которых нет в целевой версии (история разошлась): bundle в каталоге сохранения + ссылка refs/peregovorka/saved/<время>.
upd_save_local_commits() { # OLD TARGET → печатает каталог сохранения
  local old="$1" target="$2" d ref
  [ "$(git -C "$REPO_ROOT" rev-list --count "$target..$old" 2>/dev/null || echo 0)" -gt 0 ] || return 0
  d="$(upd_saved_dir)"; ref="refs/peregovorka/saved/$(date +%Y%m%d-%H%M%S)"
  git -C "$REPO_ROOT" update-ref "$ref" "$old" 2>/dev/null
  git -C "$REPO_ROOT" bundle create "$d/local-commits.bundle" "$ref" "^$target" >/dev/null 2>&1
  git -C "$REPO_ROOT" log --oneline "$target..$old" > "$d/local-commits.txt" 2>/dev/null
  cat > "$d/README.txt" <<EOF
Коммиты, сделанные на сервере и отсутствующие в устанавливаемой версии (история разошлась с origin), $(date -Is).
Обновление их сохранило и перешло на целевую версию. Ничего не удалено.
  local-commits.txt     — список
  local-commits.bundle  — сами коммиты (git -C $REPO_ROOT fetch $d/local-commits.bundle)
В репозитории они доступны по ссылке $ref (git -C $REPO_ROOT log $ref).
EOF
  printf '%s\n' "$d"
}

# Куда обновляться, если канал не определяется по HEAD: релизные теги vX.Y.Z (штатный канал установщика), иначе ветка по умолчанию origin.
upd_default_target() {
  local t
  t="$(upd_release_target)"; [ -n "$t" ] && { printf '%s\n' "$t"; return 0; }
  t="$(git -C "$REPO_ROOT" rev-parse --verify --quiet 'refs/remotes/origin/HEAD^{commit}' 2>/dev/null)" && { printf '%s\n' "$t"; return 0; }
  git -C "$REPO_ROOT" rev-parse --verify --quiet 'refs/remotes/origin/main^{commit}' 2>/dev/null
}

# Обслуживание диска перед сборкой (update.sh вызывает ДО Preflight и ещё раз перед сборкой). Причина: на стенде ~28 ГБ кэша сборки BuildKit
# заняли диск, и обновление остановилось на Preflight — прежняя очистка шла только после него.
# Цели: на диске Docker свободно ≥ UPD_MIN_FREE_GB (сборка образов; по умолчанию 15 ГБ); если DATA_ROOT на той же файловой системе —
# ещё и ≥ MIN_FREE_DISK_GB (требование Preflight, 30 ГБ). Гигиена: кэш сборки больше UPD_BUILD_CACHE_MAX_GB (20 ГБ) подрезается и при достатке места.
# Ступени — по возрастанию цены; каждая только пока цель не достигнута, после каждой — новый замер и строка в журнале обновления:
#   1. неиспользуемый кэш сборки старше UPD_BUILD_CACHE_KEEP_DAYS (7) дней — слои текущей версии остаются, следующая сборка не дольше;
#   2. «висячие» образы (без тега и без контейнеров) — в том числе промежуточные слои классического builder'а;
#   3. кэш сборки сверх UPD_BUILD_CACHE_KEEP_GB (8 ГБ) — остаются последние использованные слои (torch, pip, npm);
#   4. образы ЭТОГО проекта, не нужные ни для работы, ни для отката: остаются работающие, собираемая версия, версия отката
#      (previous_git_commit: <sha12> и prev-<sha12>) и ещё UPD_KEEP_IMAGE_VERSIONS (2) самые новые версии;
#   5. весь неиспользуемый кэш сборки — следующая сборка дольше (зависимости скачиваются заново), данные не затрагиваются.
# Никогда: тома, контейнеры, чужие образы (postgres, redis, LiveKit, Egress, SIP — тоже), DATA_ROOT, модели, резервные копии, docker system prune.
# Кэш сборки общий для всего Docker на сервере: ступени 1, 3, 5 затрагивают и кэш других проектов (только неиспользуемый — идущие сборки защищены).
# UPD_DISK_MAINTENANCE=0 — выключить (тогда только замер и предупреждение).
_disk_free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {print int($4/1048576)}'; }
_docker_root() { local r; r="$(timeout 20 docker info -f '{{.DockerRootDir}}' 2>/dev/null)"; [ -d "$r" ] || r=/var/lib/docker; [ -d "$r" ] || r=/; printf '%s\n' "$r"; }
_build_cache_gb() { # размер кэша сборки (целые ГБ, с округлением вверх); пусто — неизвестно
  timeout 60 docker system df --format '{{.Type}}|{{.Size}}' 2>/dev/null | awk -F'|' '$1=="Build Cache" {
    v=$2; n=v+0; u=v; gsub(/[0-9.]/, "", u); m=1/1e9
    if (u=="B") m=1/1e9; else if (u=="kB"||u=="KB") m=1/1e6; else if (u=="MB") m=1/1e3; else if (u=="GB") m=1; else if (u=="TB") m=1000
    g=n*m; printf "%d\n", (g==int(g) ? g : int(g)+1) }'
}
_builder_keep_flag() { docker builder prune --help 2>&1 | grep -q -- '--reserved-space' && echo --reserved-space || echo --keep-storage; }
_disk_same_fs() { [ -n "$1" ] && [ -n "$2" ] && [ "$(stat -c %d "$1" 2>/dev/null)" = "$(stat -c %d "$2" 2>/dev/null)" ] && [ -n "$(stat -c %d "$1" 2>/dev/null)" ]; }

# Какие образы проекта нельзя удалять: печатает «репозиторий:тег» по строке.
_disk_keep_images() {
  local prev keep=() v
  prev="$(upd_state_get previous_git_commit 2>/dev/null)"; prev="${prev:0:12}"
  docker ps -a --format '{{.Image}}' 2>/dev/null
  for v in "${IMAGE_TAG:-}" "$prev"; do [ -n "$v" ] && keep+=("${v:0:12}"); done
  # ещё UPD_KEEP_IMAGE_VERSIONS самых новых версий (по времени создания образа backend; prev- и обычный тег — одна версия)
  while IFS= read -r v; do [ -n "$v" ] && keep+=("$v"); done < <(docker images --format '{{.CreatedAt}}|{{.Tag}}' "${COMPOSE_PROJECT_NAME}-backend" 2>/dev/null \
      | sort -r | cut -d'|' -f2 | sed 's/^prev-//' | grep -E '^[0-9a-f]{7,40}$' | awk '!s[$0]++' | head -n "${UPD_KEEP_IMAGE_VERSIONS:-2}")
  for v in "${keep[@]}"; do printf '%s\n' "$v"; done | awk '!s[$0]++' | while IFS= read -r v; do
    printf '%s\n' "${COMPOSE_PROJECT_NAME}-backend:$v" "${COMPOSE_PROJECT_NAME}-asr:$v" "${COMPOSE_PROJECT_NAME}-web:$v" "${COMPOSE_PROJECT_NAME}-livekit:$v" \
                  "${COMPOSE_PROJECT_NAME}-backend:prev-$v" "${COMPOSE_PROJECT_NAME}-asr:prev-$v" "${COMPOSE_PROJECT_NAME}-web:prev-$v" "${COMPOSE_PROJECT_NAME}-livekit:prev-$v"
  done
}

upd_disk_maintenance() { # [нужно_ГБ_на_диске_Docker] → 0 — цель достигнута (или замер невозможен), 1 — места всё ещё мало
  local need="${1:-${UPD_MIN_FREE_GB:-15}}" root probe target same=0 free0 free cache0 cache step keep img n=0 kf
  root="$(_docker_root)"
  [ -n "$(_disk_free_gb "$root")" ] || { warn "Место на диске Docker определить не удалось — обслуживание кэша пропущено"; return 0; }
  target="$need"
  if [ -n "${DATA_ROOT:-}" ]; then
    probe="$DATA_ROOT"; while [ ! -e "$probe" ] && [ "$probe" != "/" ]; do probe="$(dirname "$probe")"; done
    if _disk_same_fs "$root" "$probe"; then same=1; [ "${MIN_FREE_DISK_GB:-30}" -gt "$target" ] && target="${MIN_FREE_DISK_GB:-30}"; fi
  fi
  free0="$(_disk_free_gb "$root")"; cache0="$(_build_cache_gb)"
  info "Диск: на диске Docker ($root) свободно ${free0} ГБ, кэш сборки ${cache0:-?} ГБ; нужно ≥ ${target} ГБ$([ "$same" = 1 ] && echo " (сборка и Preflight: DATA_ROOT на том же диске)")"
  _ok_now() { [ "$(_disk_free_gb "$root")" -ge "$target" ] && { [ -z "$(_build_cache_gb)" ] || [ "$(_build_cache_gb)" -le "${UPD_BUILD_CACHE_MAX_GB:-20}" ]; }; }
  _ok_now && return 0
  if [ "${UPD_DISK_MAINTENANCE:-1}" = 0 ]; then
    warn "Обслуживание кэша выключено (UPD_DISK_MAINTENANCE=0): свободно ${free0} ГБ, кэш сборки ${cache0:-?} ГБ"; [ "$free0" -ge "$target" ]; return
  fi
  kf="$(_builder_keep_flag)"
  _step() { # описание команда…
    local what="$1" f1 c1; shift
    step=$((step + 1))
    "$@" >/dev/null 2>&1 || warn "  ступень ${step} ($what): команда завершилась с ошибкой — продолжаю"
    f1="$(_disk_free_gb "$root")"; c1="$(_build_cache_gb)"
    ok "  ступень ${step} — ${what}: свободно ${f1} ГБ, кэш сборки ${c1:-?} ГБ"
  }
  step=0
  _step "кэш сборки старше ${UPD_BUILD_CACHE_KEEP_DAYS:-7} дней" timeout 1800 docker builder prune -f --filter "until=$(( ${UPD_BUILD_CACHE_KEEP_DAYS:-7} * 24 ))h"
  _ok_now || _step "образы без тега" timeout 600 docker image prune -f
  _ok_now || _step "кэш сборки сверх ${UPD_BUILD_CACHE_KEEP_GB:-8} ГБ (последние слои остаются)" timeout 1800 docker builder prune -f "$kf" "${UPD_BUILD_CACHE_KEEP_GB:-8}gb"
  if [ "$(_disk_free_gb "$root")" -lt "$target" ]; then
    keep="$(_disk_keep_images)"
    while IFS= read -r img; do
      [ -n "$img" ] || continue
      grep -qxF "$img" <<<"$keep" && continue
      timeout 300 docker rmi "$img" >/dev/null 2>&1 && n=$((n + 1))
    done < <(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E "^${COMPOSE_PROJECT_NAME}-(backend|asr|web|livekit):(prev-)?[0-9a-f]{7,40}$")
    step=$((step + 1)); ok "  ступень ${step} — старые образы проекта (откат и работающие сохранены): удалено тегов ${n}, свободно $(_disk_free_gb "$root") ГБ"
  fi
  [ "$(_disk_free_gb "$root")" -ge "$target" ] || _step "весь неиспользуемый кэш сборки (следующая сборка дольше)" timeout 1800 docker builder prune -f
  free="$(_disk_free_gb "$root")"; cache="$(_build_cache_gb)"
  upd_action "обслуживание диска: свободно ${free0} → ${free} ГБ, кэш сборки ${cache0:-?} → ${cache:-?} ГБ"
  if [ "$free" -ge "$target" ]; then ok "Диск: свободно ${free} ГБ (было ${free0}), кэш сборки ${cache:-?} ГБ (был ${cache0:-?}) — достаточно"; return 0; fi
  warn "Диск: и после очистки свободно ${free} ГБ (нужно ≥ ${target}). Данные, модели, резервные копии и образы отката не удаляются автоматически — освободите место на сервере (что занимает: du -xh --max-depth=1 ${DATA_ROOT:-/} | sort -h | tail; docker system df)"
  return 1
}
