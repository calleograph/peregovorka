#!/usr/bin/env bash
# Функции локального (аварийного) администратора: вызов служебной команды в контейнере backend и заметный блок с данными для входа.
# Подключается через `source` из scripts/common.sh-совместимых скриптов; сама ничего не выполняет.
#
# Пароль печатается ТОЛЬКО в терминал (/dev/tty, а если его нет — в stdout) и нигде не сохраняется: ни в файлах, ни в .env, ни в журналах установки.

if [ -n "${_VM_ADMINLIB_LOADED:-}" ]; then return 0; fi
_VM_ADMINLIB_LOADED=1

# admin_cli ARGS… — запускает `python -m app.cli ARGS…` в контейнере backend; печатает его stdout.
admin_cli() {
  dc exec -T backend python -m app.cli "$@"
}

# cli_value KEY TEXT → значение строки KEY=… из вывода служебной команды.
cli_value() {
  printf '%s\n' "$2" | sed -n "s/^$1=//p" | head -1
}

# Куда печатать секреты: терминал пользователя, а не файл журнала (если запуск идёт через tee/перенаправление).
secret_out() {
  if [ -w /dev/tty ] && : > /dev/tty 2>/dev/null; then printf '%s' /dev/tty; else printf '%s' /dev/stdout; fi
}

# print_credentials_block URL USER PASSWORD [first|reset]
print_credentials_block() {
  local url="$1" user="$2" pass="$3" kind="${4:-first}" out b r y w
  out="$(secret_out)"
  if [ "$out" = "/dev/tty" ] || [ -t 1 ]; then b=$'\033[1m'; r=$'\033[1;31m'; y=$'\033[1;33m'; w=$'\033[0m'; else b=''; r=''; y=''; w=''; fi
  {
    printf '\n'
    printf '%s╔══════════════════════════════════════════════════════════════════════╗%s\n' "$y" "$w"
    if [ "$kind" = "reset" ]; then
      printf '%s  ДОСТУП ЛОКАЛЬНОГО АДМИНИСТРАТОРА PEREGOVORKA ВОССТАНОВЛЕН%s\n' "$b" "$w"
    else
      printf '%s  ПЕРВИЧНЫЙ ВХОД В PEREGOVORKA%s\n' "$b" "$w"
    fi
    printf '%s╚══════════════════════════════════════════════════════════════════════╝%s\n' "$y" "$w"
    printf '  URL:                      %s%s%s\n' "$b" "$url" "$w"
    printf '  Локальный администратор:  %s%s%s\n' "$b" "$user" "$w"
    printf '  %s:  %s%s%s\n' "$([ "$kind" = "reset" ] && echo 'Новый пароль         ' || echo 'Первичный пароль     ')" "$r" "$pass" "$w"
    printf '\n'
    printf '  %sСОХРАНИТЕ ЭТИ ДАННЫЕ. ПАРОЛЬ ПОКАЗЫВАЕТСЯ ТОЛЬКО СЕЙЧАС.%s\n' "$r" "$w"
    printf '  При первом входе система попросит заменить пароль на свой.\n'
    printf '\n'
    printf '  Потеряли доступ? На этом сервере, от root:\n'
    printf '      %s%s/scripts/admin-reset.sh%s     — выдаст новый пароль локального администратора\n' "$b" "${REPO_ROOT:-.}" "$w"
    printf '\n'
  } > "$out"
}

# print_next_steps URL — что делать после первого входа (без секретов; идёт в обычный вывод)
print_next_steps() {
  log
  log "Дальше — в браузере (адрес и пароль показаны выше):"
  log "  1) войдите локальным администратором и задайте свой пароль;"
  log "  2) мастер настройки проведёт по шагам: LDAP → сертификаты CA → группы администраторов → хранилище → почта → проверка системы;"
  log "  3) любой шаг можно пропустить и вернуться позже: Администрирование."
  log "Обновление до новой версии:  cd ${REPO_ROOT:-.} && ./scripts/update.sh   (или кнопка в Администрирование → Обновления и версии)"
}
