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
  local d="${BACKUP_DIR:-$DATA_ROOT/backups}/local-changes-$(date +%Y%m%d-%H%M%S)"
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

# Место на диске перед сборкой: меньше UPD_MIN_FREE_GB — очищаются кэш сборки Docker, «висячие» образы и старые образы ЭТОГО проекта
# (кроме работающих, предыдущей версии для отката и собираемой). Тома, данные, модели и чужие образы не трогаются.
upd_free_space() { # [нужно_ГБ]
  local need="${1:-${UPD_MIN_FREE_GB:-6}}" root img keep
  root="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)"; [ -d "$root" ] || root=/var/lib/docker; [ -d "$root" ] || root=/
  _upd_free_gb() { df -Pk "$root" 2>/dev/null | awk 'NR==2 {print int($4/1048576)}'; }
  [ -n "$(_upd_free_gb)" ] || return 0                                   # размер не определить — не чистим наугад
  [ "$(_upd_free_gb)" -ge "$need" ] && return 0
  warn "Автопочинка: на диске Docker свободно $(_upd_free_gb) ГБ (нужно ≥ $need ГБ для сборки) — очищаю кэш сборки и старые образы проекта"
  docker builder prune -f >/dev/null 2>&1
  docker image prune -f >/dev/null 2>&1                                   # только «висячие» (без тега), не используемые
  keep="$(docker ps -a --format '{{.Image}}' 2>/dev/null | sort -u)"
  while IFS= read -r img; do
    [ -n "$img" ] || continue
    case "$img" in *":${IMAGE_TAG:-none}"|*":prev-"*|*":latest") continue ;; esac
    grep -qxF "$img" <<<"$keep" && continue
    docker rmi "$img" >/dev/null 2>&1
  done < <(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E "^${COMPOSE_PROJECT_NAME}-(backend|asr|web|livekit):[0-9a-f]{7,40}$")
  ok "После очистки свободно $(_upd_free_gb) ГБ"
  [ "$(_upd_free_gb)" -ge "$need" ] || warn "Места всё ещё мало ($(_upd_free_gb) ГБ): сборка может не уместиться — освободите место на диске сервера"
}
