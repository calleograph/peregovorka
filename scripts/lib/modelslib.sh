#!/usr/bin/env bash
# modelslib.sh — модель распознавания речи (ASR): готовность файлов, права доступа, проверка чтения от имени сервиса, разбор причины падения сервиса.
# Только функции. Зависит от common.sh (load_env выполнен). Права правятся ТОЛЬКО внутри ${DATA_ROOT}/models; chmod 777 не используется.

if [ -n "${_VM_MODELSLIB_LOADED:-}" ]; then return 0; fi
_VM_MODELSLIB_LOADED=1

ASR_MIN_CKPT_BYTES=$((1024 * 1024))   # заведомо меньше любого реального чекпойнта: отсекает HTML-заглушки и обрывы

_m_size() { stat -c '%s' "$1" 2>/dev/null || wc -c < "$1" 2>/dev/null || echo 0; }

# Модель готова только когда на месте ВСЕ файлы: чекпойнт нормального размера и токенайзер.
asr_model_ready() { # [имя_модели]
  local d="${DATA_ROOT}/models/gigaam" n="${1:-${ASR_MODEL_NAME:-v3_e2e_rnnt}}"
  [ -f "$d/$n.ckpt" ] && [ "$(_m_size "$d/$n.ckpt")" -ge "$ASR_MIN_CKPT_BYTES" ] && [ -s "$d/${n}_tokenizer.model" ]
}

# Приводит права внутри ${DATA_ROOT}/models к «читаемо для непривилегированного пользователя контейнера ASR (uid 10001)»:
# каталоги u=rwx,go=rx, файлы +r для группы и остальных. Владелец не меняется, ссылки не раскрываются, остальные каталоги данных не трогаются.
# Код 1 — исправить не удалось (в stderr — конкретный путь).
models_fix_perms() {
  local root="${DATA_ROOT}/models" bad first sudo=""
  [ -d "$root" ] || return 0
  [ ! -L "$root" ] || { warn "$root — символическая ссылка: права не меняются"; return 1; }
  _m_bad() { find -P "$root" \( -type d \( ! -perm -u=rwx -o ! -perm -g=rx -o ! -perm -o=rx \) \) -o \( -type f ! -perm -g=r -o -type f ! -perm -o=r \) 2>/dev/null; }
  bad="$(_m_bad | head -1)"
  [ -z "$bad" ] && return 0
  first="$bad"
  _m_apply() {
    find -P "$root" -type d \( ! -perm -u=rwx -o ! -perm -g=rx -o ! -perm -o=rx \) -exec $sudo chmod u+rwx,go+rx {} + 2>/dev/null
    find -P "$root" -type f \( ! -perm -g=r -o ! -perm -o=r \) -exec $sudo chmod go+r {} + 2>/dev/null
  }
  _m_apply
  if [ -n "$(_m_bad | head -1)" ] && [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then sudo=sudo; sudo -n true 2>/dev/null && _m_apply; fi
  bad="$(_m_bad | head -1)"
  if [ -n "$bad" ]; then
    fail "Нет прав исправить доступ к модели: $bad (владелец $(stat -c '%U' "$bad" 2>/dev/null)). Выполните: sudo chmod -R u+rwX,go+rX $root"
    return 1
  fi
  ok "Права на каталог моделей приведены в порядок (читаются сервисом ASR): $root [первое исправленное: ${first#"$root"/}]"
}

# Реальное чтение файлов модели от имени пользователя, под которым работает ASR (одноразовый контейнер из образа asr, каталог — как в compose, только чтение).
# Код 0 — всё читается; 1 — печатает путь и причину.
models_access_check() {
  local name="${ASR_MODEL_NAME:-v3_e2e_rnnt}" out
  out="$(dc run --rm --no-deps -T --entrypoint python asr -c '
import os, sys
root, name = "/models/gigaam", sys.argv[1]
need = [f"{name}.ckpt", f"{name}_tokenizer.model"]
try:
    for n in need:
        open(os.path.join(root, n), "rb").read(1)
    for d, _dirs, files in os.walk(root, onerror=lambda e: (_ for _ in ()).throw(e)):
        for f in files:
            open(os.path.join(d, f), "rb").read(1)
except OSError as e:
    print(f"{type(e).__name__}: {e}"); sys.exit(1)
' "$name" 2>&1)" && return 0
  printf '%s\n' "$out" | tail -3 | mask_stream >&2
  return 1
}

# Первопричина падения сервиса по последним строкам его журнала (секреты маскируются). Печатает причину и безопасное действие;
# DIAG_FIX=models — исправление прав каталога моделей возможно автоматически.
DIAG_FIX=""
diagnose_service() { # сервис
  local s="$1" lines hit hint=""
  lines="$(dc logs --no-color --tail 120 "$s" 2>&1 | mask_stream)"
  [ -n "$lines" ] || { warn "Сервис $s: журнал пуст (контейнер не создан или не запускался)"; return 0; }
  hit="$(printf '%s\n' "$lines" | grep -E 'PermissionError|Permission denied|FileNotFoundError|No such file|ModuleNotFoundError|ImportError|Address already in use|MemoryError|Killed|OperationalError|Traceback|Error|FATAL' | grep -v '^Traceback' | tail -2)"
  case "$hit" in
    *PermissionError*|*"Permission denied"*)
      case "$hit" in *models*) DIAG_FIX=models; hint="сервис не может читать файлы модели в ${DATA_ROOT}/models (права). Исправление только внутри этого каталога: sudo scripts/models.sh" ;; *) hint="нет прав на файл или каталог из сообщения выше; проверьте владельца и режим этого пути" ;; esac ;;
    *FileNotFoundError*|*"No such file"*) case "$hit" in *models*|*.ckpt*) hint="нет файла модели ASR: scripts/models.sh (при закрытой сети — scripts/models.sh --from-dir КАТАЛОГ)" ;; *) hint="не найден файл из сообщения выше" ;; esac ;;
    *ModuleNotFoundError*|*ImportError*) hint="образ собран неполно или повреждён: scripts/install.sh --from build --force-build (данные и PostgreSQL не затрагиваются)" ;;
    *"Address already in use"*) hint="порт занят другим процессом: ss -ltnp, затем измените порт в .env или остановите процесс" ;;
    *MemoryError*|*Killed*) hint="не хватает памяти (процесс убит ядром): освободите память или уменьшите нагрузку (ASR_MAX_CONCURRENT_INFERENCE)" ;;
  esac
  warn "Сервис $s не готов. Последние строки журнала:"
  printf '%s\n' "$lines" | tail -12 | sed 's/^/    | /' >&2
  [ -n "$hit" ] && warn "Вероятная первопричина: $(printf '%s' "$hit" | tail -1 | cut -c1-240)"
  [ -n "$hint" ] && warn "Что делать: $hint"
  return 0
}

# Сервисы проекта, которые не работают (остановлены, перезапускаются, unhealthy) — для разбора после неудачного ожидания.
diagnose_failed_services() {
  local s cid st h
  for s in asr backend livekit web; do
    cid="$(dc ps -aq "$s" 2>/dev/null | head -1)"
    [ -n "$cid" ] || continue
    st="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"
    h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null)"
    if [ "$st" != running ] || [ "$h" = unhealthy ] || [ "$h" = starting ]; then diagnose_service "$s"; fi
  done
}
