# shellcheck shell=bash
# Подключается из run.sh: update.sh / rollback.sh / updatelib.sh, Dockerfile, закрепление версий. Используются: ROOT, TMP, t, eq.
UL="$ROOT/scripts/lib/common.sh"
export UL ROOT TMP BIN

# ---------------------------------------------------------------------------------------- Dockerfile
for f in backend/Dockerfile asr-service/Dockerfile frontend/Dockerfile deployment/livekit/Dockerfile; do
  t "$f: нет буквальных \\n и склеенных строк (причина apt exit 100)" bash -c '! grep -nE "\\\\n[[:space:]]" "$1/$2"' _ "$ROOT" "$f"
done
t "apt: Acquire::Retries=5 для update и install (backend и ASR)" bash -c 'for f in backend asr-service; do [ "$(grep -c "Acquire::Retries=5" "$1/$f/Dockerfile")" -ge 2 ] || exit 1; done' _ "$ROOT"
t "pip/npm/git: повторы при сетевых сбоях (PIP_RETRIES, цикл повтора, NPM fetch-retries)" bash -c 'grep -q "PIP_RETRIES=5" "$1/backend/Dockerfile" && grep -q "PIP_RETRIES=5" "$1/asr-service/Dockerfile" && grep -q "gigaam: сбой, повтор" "$1/asr-service/Dockerfile" && grep -q "NPM_CONFIG_FETCH_RETRIES=5" "$1/frontend/Dockerfile" && grep -q "npm ci: сбой, повтор" "$1/frontend/Dockerfile"' _ "$ROOT"
t "draw.io: версия закреплена тегом, клонирование с повторами, лишнее вырезается (prune.sh)" bash -c 'grep -qE "^ARG DRAWIO_VERSION=v[0-9]+\.[0-9]+\.[0-9]+$" "$1/frontend/Dockerfile" && grep -q "git clone drawio: сбой, повтор" "$1/frontend/Dockerfile" && grep -q "prune.sh" "$1/frontend/Dockerfile" && grep -q "COPY --from=drawio" "$1/frontend/Dockerfile"' _ "$ROOT"
t "web nginx: /drawio/ — собственный CSP, CORS для sandbox-фрейма и SAMEORIGIN; unsafe-eval только там" bash -c 'f="$1/frontend/nginx.conf"; blk="$(awk "/location \/drawio\//,/^    }/" "$f")"; printf "%s" "$blk" | grep -q "Access-Control-Allow-Origin" && printf "%s" "$blk" | grep -q "X-Frame-Options \"SAMEORIGIN\"" && printf "%s" "$blk" | grep -q "frame-ancestors .self." && printf "%s" "$blk" | grep -q "unsafe-eval" && [ "$(grep -c "unsafe-eval" "$f")" = 1 ]' _ "$ROOT"
t "web nginx: приложение разрешает фреймы только своего origin (frame-src 'self'), сам по себе не встраивается" bash -c 'f="$1/frontend/nginx.conf"; [ "$(grep -c "frame-src .self.; frame-ancestors .none." "$f")" = 2 ]' _ "$ROOT"
DX="$TMP/dx"; mkdir -p "$DX/clone/src/main/webapp/js/diagramly" "$DX/clone/src/main/webapp/js/jquery" "$DX/clone/src/main/webapp/resources" "$DX/clone/src/main/webapp/images" "$DX/clone/src/main/webapp/WEB-INF" "$DX/clone/src/main/webapp/templates"
for p in index.html js/app.min.js js/integrate.min.js js/diagramly/Dropbox.js js/jquery/j.js resources/dia.txt resources/dia_ru.txt resources/dia_fr.txt images/a.png WEB-INF/web.xml templates/t.xml github.html; do echo x > "$DX/clone/src/main/webapp/$p"; done; echo LIC > "$DX/clone/LICENSE"
t "drawio/prune.sh: оставляет редактор, ru/en и лицензию; вырезает интеграции, шаблоны, WEB-INF, лишние языки" bash -c 'sh "$1/frontend/drawio/prune.sh" "$2/clone" "$2/out" >/dev/null && cd "$2/out" && [ -f index.html ] && [ -f js/app.min.js ] && [ -f resources/dia_ru.txt ] && [ -f LICENSE ] && [ -f images/a.png ] && [ ! -e js/integrate.min.js ] && [ ! -e js/diagramly ] && [ ! -e resources/dia_fr.txt ] && [ ! -e WEB-INF ] && [ ! -e templates ] && [ ! -e github.html ]' _ "$ROOT" "$DX"
t "drawio/prune.sh: не клон draw.io → понятная ошибка, ничего не создаёт" bash -c 'mkdir -p "$2/bad"; ! sh "$1/frontend/drawio/prune.sh" "$2/bad" "$2/out2" 2>/dev/null && [ ! -e "$2/out2" ]' _ "$ROOT" "$DX"
t "кэш: commit/время сборки объявляются ПОСЛЕ установки зависимостей (смена commit не сбрасывает слои torch/pip)" bash -c '
  for f in backend asr-service; do
    last="$(grep -n "pip install" "$1/$f/Dockerfile" | tail -1 | cut -d: -f1)"; first="$(grep -n "APP_GIT_COMMIT" "$1/$f/Dockerfile" | head -1 | cut -d: -f1)"
    [ -n "$first" ] && [ "$first" -gt "$last" ] || exit 1
  done' _ "$ROOT"
t "web: version.json с commit/временем сборки и nginx отдаёт его без кэша" bash -c 'grep -q "dist/version.json" "$1/frontend/Dockerfile" && grep -q "location = /version.json" "$1/frontend/nginx.conf" && grep -q "APP_GIT_COMMIT" "$1/deployment/compose.yml"' _ "$ROOT"
t "compose передаёт APP_GIT_COMMIT/APP_BUILT_AT всем собираемым образам (backend, asr, web)" bash -c '[ "$(grep -c "APP_GIT_COMMIT: " "$1/deployment/compose.yml")" -ge 3 ] && [ "$(grep -c "APP_BUILT_AT: " "$1/deployment/compose.yml")" -ge 3 ]' _ "$ROOT"
t "host_version_info: commit из git и время сборки (APP_BUILD_TIME учитывается)" bash -c 'source "$1"; APP_BUILD_TIME=2026-10-06T10:00:00Z host_version_info; [ "$APP_BUILT_AT" = 2026-10-06T10:00:00Z ] && [ "$APP_GIT_COMMIT" != unknown ] && [ ${#APP_GIT_COMMIT} -eq 12 ]' _ "$UL"

# ------------------------------------------------------------------------------------- .env: параметры
EX="$TMP/ex.env"; EV="$TMP/cur.env"
cat > "$EX" <<'EOF'
# комментарий
COMPOSE_PROJECT_NAME=example
ASR_INTEROP_THREADS=0
ASR_MODEL_ID=gigaam-v3-e2e-rnnt-full
ASR_GGUF_ARGS="-m {model} -f {wav} -t {threads}"
LIVEKIT_ROOM_DEPARTURE_TIMEOUT=60
NEW_API_KEY=CHANGE_ME_key
NEW_SECRET_VALUE=abcdef
NEW_LISTEN_PORT=18999
NGINX_NEW_THING=x
LDAP_NEW_OPTION=y
OPTIONAL_EMPTY=
EOF
cat > "$EV" <<'EOF'
COMPOSE_PROJECT_NAME=prod
LDAP_BIND_PASSWORD=Sup3r$ecret#Pass
APP_MASTER_KEY=masterkey123456
EOF
cls() { bash -c 'source "$1"; upd_env_classify "$2" "$3"; echo "SAFE:${UPD_NEW_SAFE[*]}"; echo "DECIDE:${UPD_NEW_DECIDE[*]}"; echo "EMPTY:${UPD_NEW_EMPTY[*]}"' _ "$UL" "$EV" "$EX"; }
OUT="$(cls)"
export EX EV; export -f cls
t "безопасные: потоки, ID модели, GGUF-аргументы, таймауты LiveKit" eq "$(sed -n 's/^SAFE://p' <<<"$OUT")" "ASR_INTEROP_THREADS ASR_MODEL_ID ASR_GGUF_ARGS LIVEKIT_ROOM_DEPARTURE_TIMEOUT"
t "требуют решения: секреты, порты, NGINX_*, LDAP_*, CHANGE_ME" eq "$(sed -n 's/^DECIDE://p' <<<"$OUT")" "NEW_API_KEY NEW_SECRET_VALUE NEW_LISTEN_PORT NGINX_NEW_THING LDAP_NEW_OPTION"
t "пустые необязательные — отдельно" eq "$(sed -n 's/^EMPTY://p' <<<"$OUT")" "OPTIONAL_EMPTY"
t "существующие переменные (COMPOSE_PROJECT_NAME) не считаются новыми" bash -c '! grep -q COMPOSE_PROJECT_NAME <<<"$1"' _ "$OUT"
t "дописывание: .env не перезаписывается — прежние строки (со спецсимволами) байт в байт на месте" bash -c '
  source "$1"; cp "$2" "$3.work"; upd_env_classify "$3.work" "$4"; upd_env_append_safe "$3.work" "$4"
  head -3 "$3.work" | cmp -s - "$2" && grep -q "^ASR_INTEROP_THREADS=0$" "$3.work" && grep -q "^LIVEKIT_ROOM_DEPARTURE_TIMEOUT=60$" "$3.work" && ! grep -q NEW_API_KEY "$3.work" && ! grep -q NEW_LISTEN_PORT "$3.work"' _ "$UL" "$EV" "$TMP/envtest" "$EX"
t "значения секретов не попадают в вывод классификации" bash -c '! grep -qE "Sup3r|masterkey|abcdef|CHANGE_ME" <<<"$1"' _ "$OUT"
t "upd_env_set_key меняет ОДНУ переменную и ничего больше" bash -c '
  source "$1"; cp "$2" "$3"; upd_env_set_key "$3" COMPOSE_PROJECT_NAME renamed >/dev/null; [ "$(sed -n 1p "$3")" = "COMPOSE_PROJECT_NAME=renamed" ] && sed -n "2,3p" "$3" | cmp -s - <(sed -n "2,3p" "$2")' _ "$UL" "$EV" "$TMP/setkey.env"

export -f cls
# ---------------------------------------------------------------------------------- LiveKit закрепление
pin() { # pin ТЕКУЩИЙ_ТЕГ → итоговый тег в файле и сообщение
  printf 'COMPOSE_PROJECT_NAME=x\nLIVEKIT_IMAGE_TAG=%s\nOTHER=1\n' "$1" > "$TMP/pin.env"
  bash -c 'source "$1"; REPO_ROOT="$2"; upd_livekit_pin "$3" 2>&1; grep "^LIVEKIT_IMAGE_TAG=" "$3"; grep -q "^OTHER=1$" "$3" || echo LOST' _ "$UL" "$ROOT" "$TMP/pin.env"
}
export -f pin
t "LiveKit latest → проверенная версия" bash -c 'out="$(pin latest)"; grep -q "^LIVEKIT_IMAGE_TAG=v1.13.7$" <<<"$out" && ! grep -q LOST <<<"$out"'
t "LiveKit v1.9.0 (устарел) → проверенная версия" bash -c 'pin v1.9.0 | grep -q "^LIVEKIT_IMAGE_TAG=v1.13.7$"'
t "LiveKit v1.13.7 — без изменений" bash -c 'pin v1.13.7 | grep -q "проверенная версия"'
t "LiveKit более новый конкретный тег — оставлен, с предупреждением" bash -c 'out="$(pin v1.20.0)"; grep -q "^LIVEKIT_IMAGE_TAG=v1.20.0$" <<<"$out" && grep -q "новее проверенной" <<<"$out"'

# ---------------------------------------------------------------------------------------------- модели
MD="$TMP/models-data"; mkdir -p "$MD/models/gigaam"
mc() { env DATA_ROOT="$MD" ASR_MODEL_NAME=v3_e2e_rnnt bash -c 'source "$1"; upd_models_check 2>&1; echo "F=$UPD_FULL_OK"' _ "$UL"; }
export MD; export -f mc
t "модели отсутствуют: Full=0, ничего не скачивается и не создаётся" bash -c 'out="$(mc)"; grep -q "F=0" <<<"$out" && [ -z "$(ls -A "$1/models/gigaam")" ]' _ "$MD"
head -c 2000000 /dev/zero > "$MD/models/gigaam/v3_e2e_rnnt.ckpt"; echo tok > "$MD/models/gigaam/v3_e2e_rnnt_tokenizer.model"
t "полная модель на месте: Full=1" bash -c 'mc | grep -q "F=1"'
t "существующие файлы моделей не изменяются проверкой" bash -c 'a="$(cksum "$1"/*)"; mc >/dev/null; [ "$a" = "$(cksum "$1"/*)" ]' _ "$MD/models/gigaam"

# --------------------------------------------------------------------------- ожидание healthcheck
WB="$TMP/wbin"; mkdir -p "$WB"; : > "$TMP/wcalls"
cat > "$WB/docker" <<'DOCK'
#!/usr/bin/env bash
# подставной docker: сервис web становится healthy после N опросов (WEB_AFTER), остальные healthy сразу; WEB_STATE=exited — упал
case "$1" in
  compose) case "$*" in *" ps -q "*) echo "cid-${@: -1}" ;; esac ;;
  inspect)
    cid="${@: -1}"; svc="${cid#cid-}"
    case "$3" in
      *State.Status*) if [ "$svc" = web ] && [ "${WEB_STATE:-}" = exited ]; then echo exited; else echo running; fi ;;
      *Health*) if [ "$svc" = web ]; then n=$(( $(cat "$WCALLS" | wc -l) )); echo x >> "$WCALLS"; if [ "$n" -ge "${WEB_AFTER:-2}" ]; then echo healthy; else echo starting; fi; else echo healthy; fi ;;
    esac ;;
esac
DOCK
chmod +x "$WB/docker"
wh() { env PATH="$WB:$PATH" WCALLS="$TMP/wcalls" COMPOSE_PROJECT_NAME=pg-w DATA_ROOT="$TMP/w-data" UPD_POLL=0 UPD_REPORT_EVERY=0 "$@" bash -c 'source "$LIB_"; upd_wait_healthy "${T_:-20}"' ; }
cat > "$TMP/waitrun.sh" <<'WR'
#!/usr/bin/env bash
source "$LIBP"; upd_wait_healthy "$1"
WR
wrun() { : > "$TMP/wcalls"; env PATH="$WB:$PATH" LIBP="$UL" WCALLS="$TMP/wcalls" COMPOSE_PROJECT_NAME=pg-w DATA_ROOT="$TMP/w-data" "$@" 2>&1; }
OW="$(wrun WEB_AFTER=3 UPD_POLL=0 UPD_REPORT_EVERY=0 bash "$TMP/waitrun.sh" 30)"
t "ожидание: пока web starting — печатает «Waiting for web... N/T sec», затем OK" bash -c 'grep -q "Waiting for web\.\.\. [0-9]*/30 sec (starting)" <<<"$1" && grep -q "Все сервисы healthy" <<<"$1"' _ "$OW"
OW="$(wrun WEB_STATE=exited UPD_POLL=0 bash "$TMP/waitrun.sh" 30)"
t "ожидание: упавший (exited) сервис — немедленный отказ с именем сервиса" bash -c 'grep -q "web:exited" <<<"$1"' _ "$OW"
OW="$(wrun WEB_AFTER=99999 UPD_POLL=1 UPD_REPORT_EVERY=100 bash "$TMP/waitrun.sh" 2)"
t "ожидание: таймаут — отказ и перечисление не готовых сервисов" bash -c 'grep -q "не стали healthy: web:starting" <<<"$1"' _ "$OW"

# -------------------------------------------------------------------------- миграции и откат (git)
GR="$TMP/gitrev"; mkdir -p "$GR/backend/migrations/versions"; git -C "$GR" init -q . 2>/dev/null
git -C "$GR" config user.email t@t; git -C "$GR" config user.name t
printf "revision: str = '0001'\ndown_revision = None\n" > "$GR/backend/migrations/versions/0001_a.py"
printf "revision: str = '0002'\ndown_revision = '0001'\n" > "$GR/backend/migrations/versions/0002_b.py"
git -C "$GR" add -A; git -C "$GR" commit -qm c1; C1="$(git -C "$GR" rev-parse HEAD)"
printf "revision: str = '0003'\ndown_revision = '0002'\n" > "$GR/backend/migrations/versions/0003_c.py"
git -C "$GR" add -A; git -C "$GR" commit -qm c2; C2="$(git -C "$GR" rev-parse HEAD)"
rv() { bash -c 'source "$1"; REPO_ROOT="$2"; upd_rev_in_commit "$3" "$4"' _ "$UL" "$GR" "$1" "$2"; }
t "ревизия 0002 известна старому коду → откат совместим" rv 0002 "$C1"
t "ревизия 0003 НЕ известна старому коду → откат остановится" bash -c '! rv 0003 "$1"' _ "$C1" 2>/dev/null || true
t "0003 неизвестна коду до миграции (C1)" bash -c 'source "$1"; REPO_ROOT="$2"; ! upd_rev_in_commit 0003 "$3"' _ "$UL" "$GR" "$C1"
t "0003 известна коду после миграции (C2)" rv 0003 "$C2"

# ---------------------------------------------------------------------- update.sh на настоящем git
mkorigin() { # создаёт $TMP/o (bare), $TMP/seed, $TMP/cl; в seed — копия scripts/ и нужных файлов
  rm -rf "$TMP/o" "$TMP/seed" "$TMP/cl" "$TMP/upd-data"
  git init -q --bare "$TMP/o" 2>/dev/null; git clone -q "$TMP/o" "$TMP/seed" 2>/dev/null
  ( cd "$TMP/seed" || exit 1; git config user.email t@t && git config user.name t && git checkout -q -b main 2>/dev/null
    mkdir -p scripts deployment/livekit deployment/nginx backend/migrations/versions asr-service frontend
    cp -r "$ROOT/scripts/." scripts/; cp "$ROOT/deployment/compat.env" deployment/; cp "$ROOT/deployment/compose.yml" deployment/
    cp "$ROOT"/deployment/nginx/* deployment/nginx/; cp "$ROOT/deployment/livekit/Dockerfile" deployment/livekit/
    cp "$ROOT/.env.example" .env.example; echo 0.1.0 > VERSION; echo x > backend/Dockerfile; echo x > asr-service/Dockerfile; echo x > frontend/Dockerfile
    printf "revision: str = '0001'\ndown_revision = None\n" > backend/migrations/versions/0001_a.py
    printf '.env\n.env.*\n!.env.example\n' > .gitignore
    git add -A; git commit -qm "первый commit" ; git push -q origin main 2>/dev/null
    git -C "$TMP/o" symbolic-ref HEAD refs/heads/main )
  git clone -q "$TMP/o" "$TMP/cl" 2>/dev/null
  git -C "$TMP/cl" config user.email t@t; git -C "$TMP/cl" config user.name t
  cat > "$TMP/cl/.env" <<EOF
COMPOSE_PROJECT_NAME=pg-upd
DATA_ROOT=$TMP/upd-data
LIVEKIT_IMAGE_TAG=latest
LDAP_BIND_PASSWORD=Sup3r\$ecret#Pass
APP_MASTER_KEY=masterkey123456
WEB_PORT=18480
EOF
  chmod 600 "$TMP/cl/.env"
}
pushnew() { # новый commit с новыми параметрами .env.example и миграцией
  ( cd "$TMP/seed" || exit 1; printf '\nNEW_SAFE_PARAM=42\nNEW_ADMIN_PASSWORD=CHANGE_ME_x\nNEW_LISTEN_PORT=19000\n' >> .env.example
    printf "revision: str = '0002'\ndown_revision = '0001'\n" > backend/migrations/versions/0002_b.py
    git add -A; git commit -qm "Добавлена функция X"; git push -q origin main 2>/dev/null )
}
UPD="$TMP/cl/scripts/update.sh"
mkorigin
t "update.sh --help работает и описывает порядок действий" bash -c 'bash "$1" --help | grep -q "git merge --ff-only"' _ "$UPD"

# up-to-date + dry-run
OUTD="$(cd "$TMP/cl" && env PATH="$BIN:$PATH" bash "$UPD" --dry-run 2>&1)"; RCD=$?
t "актуальная версия: сообщает, что обновлений нет" bash -c 'grep -q "Новых commit" <<<"$1"' _ "$OUTD"

pushnew
H0="$(git -C "$TMP/cl" rev-parse HEAD)"
OUTD="$(cd "$TMP/cl" && env PATH="$BIN:$PATH" bash "$UPD" --dry-run 2>&1)"; RCD=$?
t "dry-run: код возврата 0" eq "$RCD" 0
t "dry-run: показывает новые commit'ы" bash -c 'grep -q "Добавлена функция X" <<<"$1"' _ "$OUTD"
t "dry-run: безопасный новый параметр будет добавлен (имя=значение)" bash -c 'grep -q "+ NEW_SAFE_PARAM=42" <<<"$1"' _ "$OUTD"
t "dry-run: параметры, требующие решения, — только имена" bash -c 'grep -q "? NEW_ADMIN_PASSWORD" <<<"$1" && grep -q "? NEW_LISTEN_PORT" <<<"$1" && ! grep -q "CHANGE_ME" <<<"$1"' _ "$OUTD"
t "dry-run: значения секретов .env нигде не печатаются" bash -c '! grep -qE "Sup3r|masterkey123456" <<<"$1"' _ "$OUTD"
t "dry-run: предупреждает о миграциях БД" bash -c 'grep -q "МИГРАЦИИ БАЗЫ ДАННЫХ" <<<"$1"' _ "$OUTD"
t "dry-run: ничего не изменено (HEAD, .env, нет резервных копий и state)" bash -c '[ "$(git -C "$1" rev-parse HEAD)" = "$2" ] && ! ls "$1"/.env.backup-* >/dev/null 2>&1 && [ ! -e "$3/state" ]' _ "$TMP/cl" "$H0" "$TMP/upd-data"

# локальные изменения: остановка, ничего не удалено и не спрятано молча
echo "локальная правка" >> "$TMP/cl/README.local"; echo "изменение" >> "$TMP/cl/.env.example"
OUTL="$(cd "$TMP/cl" && env PATH="$BIN:$PATH" bash "$UPD" --yes 2>&1)"; RCL=$?
t "локальные изменения: код возврата 2, обновление не продолжается" eq "$RCL" 2
t "локальные изменения: понятное сообщение и предложение stash" bash -c 'grep -q "Обнаружены локальные изменения" <<<"$1" && grep -q "Обновление остановлено" <<<"$1" && grep -q -- "--stash" <<<"$1"' _ "$OUTL"
t "локальные изменения: файлы на месте, stash не создан, HEAD прежний" bash -c '[ -f "$1/README.local" ] && grep -q "изменение" "$1/.env.example" && [ -z "$(git -C "$1" stash list)" ] && [ "$(git -C "$1" rev-parse HEAD)" = "$2" ]' _ "$TMP/cl" "$H0"
t "локальные изменения: секреты не напечатаны" bash -c '! grep -qE "Sup3r|masterkey123456" <<<"$1"' _ "$OUTL"

# сквозной прогон до остановки на ожидании сервисов (подставной docker без контейнеров)
UBIN="$TMP/ubin"; mkdir -p "$UBIN"
cat > "$UBIN/docker" <<'DOCK'
#!/usr/bin/env bash
case "$1" in
  version) echo 29.1.3 ;;
  compose) case "$2" in version) echo 2.40.3 ;; esac ;;
  info) echo overlayfs ;;
  image) exit 0 ;;
  inspect) exit 1 ;;
  *) exit 0 ;;
esac
DOCK
chmod +x "$UBIN/docker"; printf '#!/usr/bin/env bash\nexit 0\n' > "$UBIN/nginx"; chmod +x "$UBIN/nginx"
OUTR="$(cd "$TMP/cl" && env PATH="$UBIN:$PATH" bash "$UPD" --yes --stash --skip-preflight --skip-models --skip-smoke --wait 3 2>&1)"; RCR=$?
t "обновление: завершилось остановкой на ожидании (нет контейнеров), код 1" eq "$RCR" 1
t "stash: локальные изменения сохранены под именем и НЕ применены автоматически" bash -c '[ "$(git -C "$1" stash list | wc -l)" -eq 1 ] && git -C "$1" stash list | grep -q "peregovorka-update-" && [ ! -e "$1/README.local" ] && grep -q "НЕ будут применены автоматически" <<<"$2"' _ "$TMP/cl" "$OUTR"
T1="$(git -C "$TMP/seed" rev-parse HEAD)"
t "код обновлён fast-forward до origin/main" bash -c '[ "$(git -C "$1" rev-parse HEAD)" = "$2" ]' _ "$TMP/cl" "$T1"
t ".env: резервная копия с правами 600 и ИДЕНТИЧНОЙ прежней копией" bash -c 'b="$(ls "$1"/.env.backup-* 2>/dev/null | head -1)"; [ -n "$b" ] && grep -qF "Sup3r\$ecret#Pass" "$b" && grep -q "^LIVEKIT_IMAGE_TAG=latest$" "$b" && { [ "$(uname -s)" != Linux ] || [ "$(stat -c %a "$b")" = 600 ]; }' _ "$TMP/cl"
t ".env: прежние значения и секреты не изменены, добавлен только безопасный параметр" bash -c 'e="$1/.env"; grep -qF "LDAP_BIND_PASSWORD=Sup3r\$ecret#Pass" "$e" && grep -q "^APP_MASTER_KEY=masterkey123456$" "$e" && grep -q "^NEW_SAFE_PARAM=42$" "$e" && ! grep -q "NEW_ADMIN_PASSWORD" "$e" && ! grep -q "NEW_LISTEN_PORT" "$e"' _ "$TMP/cl"
t ".env: LiveKit закреплён на проверенной версии (latest → v1.13.7)" bash -c 'grep -q "^LIVEKIT_IMAGE_TAG=v1.13.7$" "$1/.env"' _ "$TMP/cl"
t "после слияния продолжает НОВАЯ версия скрипта (фаза 2) и печатает прогресс этапов" bash -c 'grep -q "Продолжает уже НОВАЯ версия" <<<"$1" && grep -q "Миграции базы данных" <<<"$1" || grep -q "Запуск сервисов" <<<"$1"' _ "$OUTR"
t "сборка: commit и время сборки передаются образам (IMAGE_TAG/версия из git, не unknown)" bash -c '! grep -q "commit=unknown" <<<"$1" && grep -q "stage=build" <<<"$1"' _ "$OUTR"
t "остановка понятна: этап и инструкция «повторите ./scripts/update.sh»" bash -c 'grep -q "Обновление остановлено на этапе" <<<"$1" && grep -q "повторите ./scripts/update.sh" <<<"$1"' _ "$OUTR"
t "состояние для отката записано: предыдущий commit, копия .env" bash -c 'f="$1/state/last-update.state"; grep -q "^previous_git_commit=$2$" "$f" && grep -q "^env_backup=.*env.backup-" "$f" && grep -q "^result=failed_at_" "$f"' _ "$TMP/upd-data" "$H0"
t "опасных операций нет: ни reset --hard, ни удаления DATA_ROOT/томов в скриптах обновления" bash -c '! grep -nE "reset --hard|rm -rf[^#]*(DATA_ROOT|models|volumes)|docker volume rm|compose down -v|system prune" "$1/scripts/update.sh" "$1/scripts/rollback.sh" "$1/scripts/lib/updatelib.sh" | grep -vE "^[^:]+:[0-9]+:[[:space:]]*#"' _ "$ROOT"
t "update.sh не вызывает install.sh и не пересоздаёт .env (cp .env.example → .env отсутствует)" bash -c '! grep -nE "(bash|exec|\./|REPO_ROOT/)scripts/install\.sh" "$1/scripts/update.sh" && ! grep -nE "(cp|mv|cat >|tee) .*\"\$ENV_FILE\"" "$1/scripts/update.sh" | grep -v backup' _ "$ROOT"

# повторный запуск после прерывания: код уже обновлён — продолжение с сборки (маркер)
OUTR2="$(cd "$TMP/cl" && env PATH="$UBIN:$PATH" bash "$UPD" --yes --skip-preflight --skip-models --skip-smoke --wait 3 2>&1)"; RCR2=$?
t "повтор после прерывания: распознаётся прерванное обновление и продолжается без повторного слияния" bash -c 'grep -q "Найдено прерванное обновление\|Новых commit'"'"'ов нет" <<<"$1"' _ "$OUTR2"

# non-ff: история разошлась — отказ без изменений
mkorigin; echo "локальный коммит" > "$TMP/cl/local.txt"; git -C "$TMP/cl" add local.txt; git -C "$TMP/cl" commit -qm "local"
( cd "$TMP/seed" || exit 1; echo y > other.txt && git add -A && git commit -qm "upstream" && git push -q origin main 2>/dev/null )
H1="$(git -C "$TMP/cl" rev-parse HEAD)"
OUTN="$(cd "$TMP/cl" && env PATH="$UBIN:$PATH" bash "$UPD" --yes 2>&1)"; RCN=$?
t "история разошлась (не fast-forward): отказ, код 1, HEAD не тронут" bash -c '[ "$1" -eq 1 ] && grep -q "не является fast-forward" <<<"$2" && [ "$(git -C "$3" rev-parse HEAD)" = "$4" ]' _ "$RCN" "$OUTN" "$TMP/cl" "$H1"

# rollback: остановка при несовместимой ревизии БД (проверка функции + текст)
t "rollback.sh: текст остановки — «git rollback ≠ database rollback» и путь к backup" bash -c 'grep -q "Git rollback ≠ database rollback" "$1/scripts/rollback.sh" && grep -q "db_backup" "$1/scripts/rollback.sh" && grep -q "exit 3" "$1/scripts/rollback.sh"' _ "$ROOT"

# ------------------------------------------------------------- проверка версий образов (verify)
cbv() { # cbv backend_commit asr_commit web_commit → вывод предупреждений
  bash -c '
    source "$1"; REPO_ROOT="$2"; VERIFY_WARNINGS=(); v_ok(){ echo "OK $*"; }; v_warn(){ echo "WARN $*"; }
    HEADC="$(git -C "$REPO_ROOT" rev-parse HEAD | cut -c1-12)"
    svc_http() { case "$1" in backend) echo "200 {\"commit\":\"${B/HEAD/$HEADC}\"}" ;; asr) echo "200 {\"commit\":\"${A/HEAD/$HEADC}\"}" ;; esac; }
    dc() { echo "{\"commit\":\"${W/HEAD/$HEADC}\"}"; }
    B="$3" A="$4" W="$5" check_build_versions' _ "$ROOT/scripts/lib/common.sh" "$ROOT" "$1" "$2" "$3"
}
export -f cbv
t "версии образов = git HEAD → OK по всем трём" bash -c 'out="$(cbv HEAD HEAD HEAD)"; [ "$(grep -c "^OK" <<<"$out")" -eq 3 ] && ! grep -q WARN <<<"$out"'
t "commit=unknown (ручная сборка) → предупреждение с командой пересборки" bash -c 'out="$(cbv unknown HEAD HEAD)"; grep -q "WARN Образ backend: commit=unknown" <<<"$out" && grep -q "rebuild.sh" <<<"$out"'
t "commit отличается от HEAD → предупреждение" bash -c 'out="$(cbv HEAD abc123 HEAD)"; grep -q "WARN Образ asr собран из commit abc123" <<<"$out"'

# ---- права на запуск (./scripts/update.sh должен запускаться без bash)
t "все scripts/*.sh (кроме lib/) имеют бит исполнения в git (иначе «Permission denied» при ./scripts/…)" bash -c 'cd "$1" && git rev-parse --git-dir >/dev/null 2>&1 || exit 0; bad="$(git ls-files -s scripts | awk "\$1!=\"100755\" && \$4 ~ /^scripts\/[^\/]+\.sh$/ {print \$4}")"; [ -z "$bad" ] || { echo "$bad" >&2; exit 1; }' _ "$ROOT"

# ------------------------------------------------------------- раздельные итоги обновления (обновление · сервисы · интеграции)
outcome() { bash -c 'source "$1/scripts/lib/common.sh"; upd_classify_outcome "$2" "$3" "$4"; printf "%s/%s" "$H_ST" "$I_ST"' _ "$ROOT" "$@"; }
t "итог: verify и smoke в порядке → здоровье ok, интеграции ok" eq "$(outcome 0 0 PASS)" "ok/ok"
t "итог: проблема LDAP (smoke 3) НЕ делает обновление неудачным: здоровье ok, интеграции fail" eq "$(outcome 0 3 INTEGRATION)" "ok/fail"
t "итог: сбой сервисов (smoke 1) → здоровье fail" eq "$(outcome 0 1 FAIL)" "fail/unknown"
t "итог: verify нашёл ошибки → здоровье fail" eq "$(outcome 2 0 PASS)" "fail/ok"
t "итог: smoke пропущен → интеграции skipped, здоровье ok" eq "$(outcome 0 0 skipped)" "ok/skipped"
t "итог: smoke-test.sh различает сбой системы (1) и сбой интеграции (3), update.sh отдаёт код 3" bash -c 'grep -q "exit 3" "$1/scripts/smoke-test.sh" && grep -q "is_integration" "$1/scripts/smoke-test.sh" && grep -q "exit 3" "$1/scripts/update.sh" && grep -q "Integrations:" "$1/scripts/update.sh"' _ "$ROOT"
t "итог: update.sh пишет четыре статуса в last-update.state" bash -c 'for k in update_status deploy_status health_status integration_status integration_issues; do grep -q "upd_state_set $k" "$1/scripts/update.sh" || exit 1; done' _ "$ROOT"
