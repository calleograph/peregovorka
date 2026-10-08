#!/usr/bin/env bash
# llm.sh — необязательная локальная модель Qwen3 1.7B Q4_K_M (контейнер llm-local-17b): включить / выключить / состояние. Нужен root (кроме status).
#
#   sudo scripts/llm.sh enable-17b  [--env FILE] [--yes] [--from-dir КАТАЛОГ]   скачать (~1,3 ГБ) и проверить файл, включить LLM_17B_ENABLED=yes, запустить контейнер
#   sudo scripts/llm.sh disable-17b [--env FILE] [--yes]                        остановить контейнер и выключить (файл модели остаётся)
#   scripts/llm.sh status [--env FILE]                                          включена ли, на месте ли файл, работает ли контейнер
#
# 1.7B заметно лучше держит содержание, чем основная Qwen3 0.6B, но втрое медленнее на CPU и занимает около 2,5 ГБ памяти (лимит контейнера LLM_17B_MEM, 3500m).
# Модель НЕ становится системной по умолчанию: она появляется в списке выбора модели (система / комната / встреча), выбирает её администратор или руководитель.
# Скрипт меняет только .env этого экземпляра и контейнеры его compose-проекта; чужие сервисы и файрвол не затрагиваются.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CMD="${1:-status}"; [ $# -gt 0 ] && shift
YES=0; FROM_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --from-dir) FROM_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name; require_vars DATA_ROOT

need_root() { [ "$(id -u)" -eq 0 ] || die "Нужны права root: sudo scripts/llm.sh $CMD"; }

case "$CMD" in
  status)
    st="$(llm17_model_state)"
    if llm17_enabled; then ok "Qwen3 1.7B включена на сервере (LLM_17B_ENABLED=yes)"; else info "Qwen3 1.7B выключена (необязательная модель). Включить: sudo scripts/llm.sh enable-17b"; fi
    log "  Файл $LLM17_FILE: $(llm_state_text "$st")"
    if llm17_enabled && command -v docker >/dev/null 2>&1; then
      cid="$(dc ps -q llm-local-17b 2>/dev/null | head -1)"
      if [ -n "$cid" ]; then log "  Контейнер llm-local-17b: $(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"; else warn "Контейнер llm-local-17b не запущен"; fi
    fi ;;
  enable-17b)
    need_root
    if [ "$YES" -ne 1 ]; then
      [ -t 0 ] || die "Нужно подтверждение: добавьте --yes"
      log "Будет скачана модель Qwen3 1.7B (~1,3 ГБ), запущен контейнер llm-local-17b (около 2,5 ГБ памяти) и перезапущен backend."
      read -r -p "Включить Qwen3 1.7B? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || die "Отменено."
    fi
    command -v docker >/dev/null 2>&1 || die "Docker не найден"
    llm17_model_fetch ${FROM_DIR:+--from-dir "$FROM_DIR"} || die "Модель не загружена: контейнер не включён (нет доступа в интернет или повреждён файл)."
    docker image inspect "$(llm_image)" >/dev/null 2>&1 || { info "Загрузка образа runtime: $(llm_image)"; docker pull "$(llm_image)" >/dev/null 2>&1 || die "Не удалось скачать образ $(llm_image)"; }
    upd_env_set_key "$ENV_FILE" LLM_17B_ENABLED yes || die "Не удалось записать LLM_17B_ENABLED в .env"
    unset LLM_17B_ENABLED; load_env "$ENV_FILE"; llm17_refresh
    info "Запуск llm-local-17b и перезапуск backend…"
    dc up -d --no-build llm-local-17b backend || die "docker compose up не выполнен (scripts/logs.sh llm-local-17b)"
    ok "Qwen3 1.7B включена. Выбрать её можно в «Настройках комнаты → Языковая модель», «Эта встреча» или в общих настройках; системной по умолчанию она не стала." ;;
  disable-17b)
    need_root
    if [ "$YES" -ne 1 ]; then
      [ -t 0 ] || die "Нужно подтверждение: добавьте --yes"
      read -r -p "Выключить Qwen3 1.7B (комнаты с этой моделью перейдут на запасной вариант по правилам LLM)? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || die "Отменено."
    fi
    dc stop llm-local-17b >/dev/null 2>&1 || true; dc rm -f llm-local-17b >/dev/null 2>&1 || true
    upd_env_set_key "$ENV_FILE" LLM_17B_ENABLED no
    unset LLM_17B_ENABLED; load_env "$ENV_FILE"; llm17_refresh
    dc up -d --no-build backend || warn "backend не перезапущен (scripts/logs.sh backend)"
    ok "Qwen3 1.7B выключена: контейнер остановлен, память освобождена. Файл модели остался на диске." ;;
  *) die "Неизвестная команда: $CMD (enable-17b | disable-17b | status)" ;;
esac
