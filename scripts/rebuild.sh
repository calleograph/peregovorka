#!/usr/bin/env bash
# rebuild.sh — ручная пересборка образов проекта ТЕМ ЖЕ способом, что и update.sh (а не «docker compose build» напрямую).
#
#   scripts/rebuild.sh [--env FILE] [--if-changed] [--pull] [--no-restart] [--wait СЕК] [backend] [asr] [web] [livekit]
#
# Зачем: обычный «docker compose build» не передаёт build-аргументы с версией и commit — образ получает «commit unknown», а verify/веб-интерфейс
# показывают, что он «собран вне штатного обновления». Этот скрипт:
#   • берёт commit и время сборки из git (APP_GIT_COMMIT, APP_BUILT_AT) и передаёт их при сборке;
#   • использует тот же выбор сборщика: BuildKit, а при инфраструктурном сбое (нет buildx, блокировка containerd) — автоматически legacy builder;
#   • повторяет сборку при временных сетевых ошибках, не пересобирает уже собранное при --if-changed;
#   • пересоздаёт только пересобранные сервисы (--no-build) и ждёт healthcheck.
# По умолчанию пересобирает указанные сервисы (без указания — все три: backend, asr, web) принудительно; --if-changed — только при изменении исходников.
# Данные, .env и модели не затрагиваются. Для обычного обновления кода используйте scripts/update.sh.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

IF_CHANGED=0; NO_RESTART=0; WAIT=180; PULL=0; SVCS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --if-changed) IF_CHANGED=1; shift ;;
    --pull) PULL=1; shift ;;
    --no-restart) NO_RESTART=1; shift ;;
    --wait) WAIT="$2"; shift 2 ;;
    backend|asr|web|livekit) SVCS+=("$1"); shift ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Неизвестный аргумент/сервис: $1 (допустимы: backend asr web livekit)" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name; require_vars DATA_ROOT
if ! pmsg="$(validate_env_paths)"; then printf '%s\n' "$pmsg" >&2; die "Некорректный путь в .env — сборка не начата."; fi

host_version_info
[ "$APP_GIT_COMMIT" != unknown ] || die "Не удалось определить git commit (каталог не является git-репозиторием?) — сборка дала бы образ с «commit unknown»."
export IMAGE_TAG="$APP_GIT_COMMIT"
[ "$IF_CHANGED" -eq 1 ] || export FORCE_BUILD=1
[ "$PULL" -eq 1 ] && export PULL_BASES=1
[ ${#SVCS[@]} -gt 0 ] || SVCS=(backend asr web)

info "Сборка: ${SVCS[*]} · версия ${APP_VERSION} · commit ${APP_GIT_COMMIT} · $APP_BUILT_AT"
build_images "${SVCS[@]}" || die "Сборка не удалась (сообщение stage=build выше; лог — $DATA_ROOT/state/build-*.log). Работающие контейнеры не тронуты."

case " ${SVCS[*]} " in *" web "*) check_web_image_config || die "Конфигурация nginx в новом образе web некорректна — работающие контейнеры не тронуты" ;; esac

if [ "$NO_RESTART" -eq 1 ]; then
  ok "Образы собраны с commit ${APP_GIT_COMMIT}. Контейнеры не перезапускались (--no-restart): примените вручную: docker compose ... up -d --no-build ${SVCS[*]}"
  exit 0
fi
info "Пересоздание: ${SVCS[*]} (--no-build)"
dc up -d --no-build "${SVCS[@]}" || die "Не удалось запустить сервисы после сборки"
upd_wait_healthy "$WAIT" || die "Сервисы не стали healthy за ${WAIT} с (docker compose ps / scripts/diag.sh)"
ok "Готово: ${SVCS[*]} пересобраны и запущены с commit ${APP_GIT_COMMIT}."
log "== Автоматическая проверка после пересборки =="
verify_deployment; VF=$?
print_verify_stages
[ "$VF" -eq 0 ] && ok "Verify: PASS" || { fail "Verify: FAIL (ошибок: $VF) — scripts/diag.sh"; exit 1; }
