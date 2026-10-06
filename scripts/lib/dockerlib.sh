#!/usr/bin/env bash
# dockerlib.sh — диагностика Docker build-подсистемы, выбор builder'а, сборка образов проекта с возобновлением.
# Подключается из common.sh. Всё ограничено объектами ТЕКУЩЕГО COMPOSE_PROJECT_NAME.
# НИКОГДА не выполняет: docker system/image/volume prune, удаление /var/lib/docker|containerd,
# размонтирование чужих mount'ов, перезапуск Docker/containerd.
#
# BUILD_MODE (в .env или окружении):  auto (по умолчанию) | buildkit | legacy
#   auto: проверяем реальную сборку через BuildKit; при инфраструктурном сбое — явный fallback на legacy builder
#         (DOCKER_BUILDKIT=0), пока установленный Docker его поддерживает.

BUILD_SERVICES=(livekit backend asr web)
SELECTED_BUILD_MODE=""

# --------------------------------------------------------------- классификация ошибок
# stdin: лог сборки → stdout: network | project | infra | unknown
#  network — временная сетевая ошибка registry/CDN/зеркала пакетов (повторяется, не ошибка проекта и не Docker);
#  project — ошибка Dockerfile/исходников (упал RUN, COPY, синтаксис);
#  infra   — сбой подсистемы Docker/containerd/диска (экспорт образа, snapshot, mount, место).
classify_build_error() {
  local t; t="$(cat)"
  if grep -qiE 'failed to copy: read tcp|tls handshake timeout|i/o timeout|connection timed out|temporary failure in name resolution|read timed out|readtimeouterror|connection reset by peer|unexpected eof|net/http: request canceled|dial tcp.*(timeout|refused)|toomanyrequests|received unexpected http status: 50[234]' <<<"$t"; then
    echo network; return
  fi
  if grep -qiE 'did not complete successfully|dockerfile parse error|unknown instruction|failed to read dockerfile|failed to compute cache key|COPY failed|lstat .*no such file|not found in build context' <<<"$t" \
     && ! grep -qiE 'containerd-mount|tmpmounts|failed to open writer|mount callback failed' <<<"$t"; then
    echo project; return
  fi
  if grep -qiE 'containerd-mount|tmpmounts|failed to open writer|ref .* locked|mount callback failed|exporting to image|unable to (create|prepare) (snapshot|extraction)|failed to (create|prepare) (snapshot|extraction)|content digest .* not found|input/output error|no space left on device|buildx component is missing|buildkit is enabled but|cannot connect to the docker daemon|error during connect' <<<"$t"; then
    echo infra; return
  fi
  echo unknown
}

# Устаревшие mount'ы containerd (только диагностика; автоматически НЕ размонтируются).
stale_containerd_mounts() { grep -E 'containerd-mount|containerd/tmpmounts' /proc/mounts 2>/dev/null || true; }

report_stale_mounts() { # report_stale_mounts warn_fn
  local n; n="$(stale_containerd_mounts | wc -l | tr -d ' ')"
  [ "${n:-0}" -gt 0 ] || return 0
  "$1" "Найдено устаревших mount'ов containerd: $n (признак прошлых сбоев экспорта образа). Установщик их НЕ трогает — ничего не размонтируется автоматически."
  stale_containerd_mounts | head -5 | sed 's/^/      /' >&2
  "$1" "Если сборка BuildKit продолжает падать: решение за администратором сервера (проверьте, что mount'ы не используются чужими процессами; `sudo umount` только для заведомо своих зависших, либо плановый перезапуск containerd вне часов работы). Пока работает fallback BUILD_MODE=legacy."
}

# ------------------------------------------------------------------------- диагностика
# docker_diag ok_fn warn_fn fail_fn — единый отчёт для preflight и installer.
docker_diag() {
  local ok="$1" warn="$2" fail="$3" v bx drv
  v="$(timeout 15 docker version --format '{{.Server.Version}}' 2>/dev/null)" || { "$fail" "Демон Docker недоступен для текущего пользователя (subsystem=docker-daemon)"; return 1; }
  "$ok" "Docker engine: $v"
  if v="$(timeout 15 docker compose version --short 2>/dev/null)"; then "$ok" "Docker Compose: $v"; else "$fail" "Плагин docker compose недоступен (subsystem=docker-compose)"; return 1; fi
  if bx="$(timeout 15 docker buildx version 2>/dev/null)"; then "$ok" "docker buildx: $(printf '%s' "$bx" | head -1)"
  else "$warn" "docker buildx НЕ установлен. Compose Bake и BuildKit-сборка через buildx недоступны; установщик использует штатный builder проекта (с проверкой) либо legacy (BUILD_MODE=auto). Пакет: docker-buildx (по желанию администратора)."; fi
  drv="$(timeout 15 docker info --format '{{.Driver}}' 2>/dev/null || true)"
  [ -n "$drv" ] && "$ok" "Storage driver: $drv ($(timeout 15 docker info --format '{{range .DriverStatus}}{{index . 0}}={{index . 1}} {{end}}' 2>/dev/null | cut -c1-120))"
  [ -n "${DOCKER_BUILDKIT:-}" ] && "$ok" "В окружении задан DOCKER_BUILDKIT=${DOCKER_BUILDKIT}"
  "$ok" "Compose Bake для сборки проекта отключается (COMPOSE_BAKE=false): сборка идёт предсказуемым builder'ом; режим: BUILD_MODE=${BUILD_MODE:-auto}"
  report_stale_mounts "$warn"
  return 0
}

# --------------------------------------------------------------------------- пробная сборка
# Реально собирает минимальный образ (FROM scratch) выбранным способом и удаляет его.
# Создаётся/удаляется ТОЛЬКО временный образ <проект>-buildprobe. Лог — в PROBE_LOG.
docker_build_probe() { # docker_build_probe buildkit|legacy
  local mode="$1" dir tag rc=0
  dir="$(mktemp -d)"; tag="${COMPOSE_PROJECT_NAME:-peregovorka}-buildprobe:$$"
  printf 'FROM scratch\nCOPY probe.txt /probe.txt\n' > "$dir/Dockerfile"; echo ok > "$dir/probe.txt"
  PROBE_LOG="$dir/probe.log"
  if [ "$mode" = "legacy" ]; then
    DOCKER_BUILDKIT=0 timeout 180 docker build -q -t "$tag" "$dir" >"$PROBE_LOG" 2>&1 || rc=$?
  else
    DOCKER_BUILDKIT=1 timeout 180 docker build -q -t "$tag" "$dir" >"$PROBE_LOG" 2>&1 || rc=$?
  fi
  docker image rm -f "$tag" >/dev/null 2>&1 || true
  PROBE_TEXT="$(cat "$PROBE_LOG" 2>/dev/null)"; rm -rf "$dir"
  return "$rc"
}

# Выбор builder'а. Результат в SELECTED_BUILD_MODE. Явно логирует fallback.
select_build_mode() {
  local want="${BUILD_MODE:-auto}"
  case "$want" in auto|buildkit|legacy) ;; *) die "BUILD_MODE должен быть auto|buildkit|legacy (сейчас: $want)" ;; esac
  if [ "$want" = "legacy" ]; then
    docker_build_probe legacy || { fail "Legacy builder не работает (subsystem=docker-build): $(printf '%s' "$PROBE_TEXT" | tail -3 | tr '\n' ' ')"; return 1; }
    SELECTED_BUILD_MODE=legacy; ok "Builder: legacy (DOCKER_BUILDKIT=0), задан BUILD_MODE=legacy"; return 0
  fi
  if docker_build_probe buildkit; then
    SELECTED_BUILD_MODE=buildkit; ok "Builder: BuildKit — пробная сборка и экспорт образа прошли"; return 0
  fi
  local kind; kind="$(printf '%s' "$PROBE_TEXT" | classify_build_error)"
  fail "Пробная сборка через BuildKit не удалась (subsystem=docker-buildkit, тип=$kind): $(printf '%s' "$PROBE_TEXT" | tail -3 | tr '\n' ' ')"
  if [ "$want" = "buildkit" ]; then return 1; fi
  warn "FALLBACK: BuildKit неработоспособен на этом сервере — переключаюсь на legacy builder (DOCKER_BUILDKIT=0). Это ошибка Docker/containerd, а не проекта."
  report_stale_mounts warn
  if docker_build_probe legacy; then
    SELECTED_BUILD_MODE=legacy; ok "Builder: legacy (DOCKER_BUILDKIT=0) — пробная сборка прошла"; return 0
  fi
  fail "Legacy builder тоже не работает (subsystem=docker-build): $(printf '%s' "$PROBE_TEXT" | tail -3 | tr '\n' ' ')"
  warn "Сборка образов на этом хосте невозможна из-за состояния Docker. Проект не затронут. Диагностика: docker info; journalctl -u docker -u containerd; df -h /var/lib/docker."
  return 1
}


# ----------------------------------------------------- повторы при сетевых сбоях registry/CDN
# retry_cmd метка попыток пауза команда… — повторяет ТОЛЬКО при временной сетевой ошибке (классификация network);
# уже скачанные слои не удаляются (никаких prune), повтор докачивает.
retry_cmd() {
  local label="$1" n="$2" pause="$3" i=1 log rc kind; shift 3
  log="$(mktemp)"
  while :; do
    info "$label: попытка $i/$n"
    "$@" 2>&1 | tee "$log"; rc=${PIPESTATUS[0]}
    [ "$rc" -eq 0 ] && { rm -f "$log"; return 0; }
    kind="$(classify_build_error < "$log")"
    if [ "$kind" = "network" ] && [ "$i" -lt "$n" ]; then
      warn "$label: временная сетевая ошибка registry/CDN (не ошибка проекта) — повтор через ${pause}с; скачанные слои сохраняются"
      sleep "$pause"; i=$((i+1)); continue
    fi
    [ "$kind" = "network" ] && fail "FAIL subsystem=registry-network: $label не выполнен за $n попытки (сеть/registry недоступны). Проверьте доступ сервера к registry и повторите — установка продолжится."
    rm -f "$log"; return "$rc"
  done
}

# Образы сторонних сервисов (PostgreSQL, Redis) — отдельно и с повторами: так сетевой сбой не выглядит ошибкой приложения.
pull_base_images() {
  retry_cmd "docker compose pull postgres redis" 3 "${PULL_RETRY_PAUSE:-10}" dc pull postgres redis
}

# ------------------------------------------------------------------- образы проекта
svc_context() { case "$1" in backend) echo backend ;; asr) echo asr-service ;; web) echo frontend ;; livekit) echo deployment/livekit ;; *) return 1 ;; esac; }
svc_image() { case "$1" in livekit) echo "${COMPOSE_PROJECT_NAME}-livekit:${LIVEKIT_IMAGE_TAG:-v1.9.0}" ;; *) echo "${COMPOSE_PROJECT_NAME}-$1:${IMAGE_TAG:-dev}" ;; esac; }
svc_extra() { case "$1" in
  livekit) echo "${LIVEKIT_IMAGE_TAG:-}" ;;
  asr) echo "${ASR_TORCH_INDEX_URL:-}|${GIGAAM_GIT_COMMIT:-}|${IMAGE_TAG:-}" ;;
  *) echo "${IMAGE_TAG:-}" ;; esac; }

src_fingerprint() { # хэш исходников контекста сборки + параметров образа
  local dir="$REPO_ROOT/$(svc_context "$1")"
  ( cd "$dir" && find . -type f -not -path './node_modules/*' -not -path './dist/*' -not -path '*/__pycache__/*' -not -name '*.pyc' -print0 \
      | LC_ALL=C sort -z | xargs -0 sha256sum 2>/dev/null; printf '%s\n' "$(svc_extra "$1")" ) | sha256sum | cut -c1-24
}

FP_FILE() { echo "${DATA_ROOT}/state/build-fingerprints"; }
fp_get() { grep "^$1 $(svc_image "$1") " "$(FP_FILE)" 2>/dev/null | tail -1 | awk '{print $3}'; }
fp_set() { # fp_set svc hash
  local f; f="$(FP_FILE)"; mkdir -p "$(dirname "$f")"
  { grep -v "^$1 $(svc_image "$1") " "$f" 2>/dev/null || true; printf '%s %s %s\n' "$1" "$(svc_image "$1")" "$2"; } > "$f.tmp" && mv "$f.tmp" "$f"
}
image_exists() { docker image inspect "$(svc_image "$1")" >/dev/null 2>&1; }

# build_needed svc → 0 если нужно собирать
build_needed() {
  [ "${FORCE_BUILD:-0}" = "1" ] && return 0
  image_exists "$1" || return 0
  [ "$(fp_get "$1")" = "$(src_fingerprint "$1")" ] && return 1
  return 0
}

# «Усыновить» уже собранные вручную образы (явное действие администратора: --adopt-images).
adopt_images() {
  local s
  for s in "${BUILD_SERVICES[@]}"; do
    if image_exists "$s"; then fp_set "$s" "$(src_fingerprint "$s")"; ok "Образ принят как актуальный: $(svc_image "$s") (исходники зафиксированы)"
    else warn "Образа $(svc_image "$s") нет — будет собран"; fi
  done
}

_compose_build_one() { # _compose_build_one svc mode
  local svc="$1" mode="$2" log="$3"
  compose_args
  if [ "$mode" = "legacy" ]; then
    env COMPOSE_BAKE=false DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker "${COMPOSE_ARGS[@]}" build "$svc" 2>&1 | tee "$log"
  else
    env COMPOSE_BAKE=false DOCKER_BUILDKIT=1 docker "${COMPOSE_ARGS[@]}" build "$svc" 2>&1 | tee "$log"
  fi
  return "${PIPESTATUS[0]}"
}

# build_images [svc…] — собирает только то, что нужно. Возврат 0/1; сообщения: stage=build service=… subsystem=…
build_images() {
  local want=("$@") s kind log mode
  [ ${#want[@]} -gt 0 ] || want=("${BUILD_SERVICES[@]}")
  [ -n "$SELECTED_BUILD_MODE" ] || select_build_mode || return 1
  mkdir -p "${DATA_ROOT}/state"
  for s in "${want[@]}"; do
    if ! build_needed "$s"; then ok "stage=build service=$s: образ $(svc_image "$s") актуален (исходники не менялись) — пропуск"; continue; fi
    log="${DATA_ROOT}/state/build-$s.log"; mode="$SELECTED_BUILD_MODE"
    info "stage=build service=$s builder=$mode → $(svc_image "$s")"
    local attempt=1 built=0
    while :; do
      if _compose_build_one "$s" "$mode" "$log"; then built=1; break; fi
      kind="$(classify_build_error < "$log")"
      if [ "$kind" = "network" ] && [ "$attempt" -lt 3 ]; then
        warn "stage=build service=$s: временная сетевая ошибка registry/CDN (попытка $attempt/3) — повтор через ${PULL_RETRY_PAUSE:-10}с; кэш слоёв сохраняется"
        sleep "${PULL_RETRY_PAUSE:-10}"; attempt=$((attempt+1)); continue
      fi
      break
    done
    if [ "$built" -eq 1 ]; then fp_set "$s" "$(src_fingerprint "$s")"; ok "stage=build service=$s: готово"; continue; fi
    if [ "$kind" = "infra" ] && [ "$mode" = "buildkit" ] && [ "${BUILD_MODE:-auto}" = "auto" ]; then
      warn "FALLBACK: сборка $s через BuildKit упала на уровне Docker/containerd (не из-за Dockerfile) — повтор через legacy builder"
      report_stale_mounts warn
      SELECTED_BUILD_MODE=legacy
      if _compose_build_one "$s" legacy "$log"; then fp_set "$s" "$(src_fingerprint "$s")"; ok "stage=build service=$s: готово (legacy builder)"; continue; fi
      kind="$(classify_build_error < "$log")"
    fi
    case "$kind" in
      network) fail "FAIL stage=build service=$s subsystem=registry-network (сеть/registry/зеркала пакетов недоступны после 3 попыток, НЕ ошибка проекта). Лог: $log" ;;
      infra)   fail "FAIL stage=build service=$s subsystem=docker-build (инфраструктура Docker/containerd, НЕ ошибка проекта). Лог: $log"; report_stale_mounts warn ;;
      project) fail "FAIL stage=build service=$s subsystem=dockerfile/project (ошибка сборки проекта). Лог: $log" ;;
      *)       fail "FAIL stage=build service=$s subsystem=unknown (см. лог: $log)" ;;
    esac
    tail -8 "$log" | sed 's/^/    | /' >&2
    return 1
  done
  return 0
}

# Проверка наличия всех образов перед up --no-build.
require_images() {
  local s miss=0
  for s in "${BUILD_SERVICES[@]}"; do image_exists "$s" || { fail "Нет образа $(svc_image "$s") — выполните этап сборки (scripts/install.sh --from build)"; miss=1; }; done
  [ "$miss" -eq 0 ]
}
