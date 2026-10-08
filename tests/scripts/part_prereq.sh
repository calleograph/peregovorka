# shellcheck shell=bash
# Подключается из run.sh: манифест системных зависимостей (scripts/lib/prereqlib.sh), определение ОС, установка «с чистой системы» (dry-run),
# стерильное окружение (sanitize_project_env) и белый список исправлений (repairlib.sh). Реальные apt/docker не вызываются.
PQ="$TMP/prereq"; mkdir -p "$PQ/bin"

# ---- 1. манифест полон: каждая внешняя программа, которую вызывают скрипты, есть в манифесте (или явно отнесена к контейнерным/необязательным)
t "манифест: загрузка" bash -c 'source "$1/scripts/lib/prereqlib.sh" && [ "${#PREREQ_PKG[@]}" -gt 30 ]' _ "$ROOT"
# Известные утилиты Linux: если скрипт вызывает любую из них, она обязана быть в манифесте (или явно отнесена к контейнерным/необязательным).
KNOWN_UTILS="jq lsof dig nslookup ping nc ncat netstat ifconfig route getent unzip zip gzip gunzip bzip2 xz rsync lsb_release iptables nft lscpu lsblk blkid losetup
  column less more vi nano dd md5sum sha1sum sha512sum shasum cksum expr bc rev paste join comm split fold fmt nl od hexdump xxd strings file which whereis whoami
  logname users who last uptime top htop vmstat iostat lsmod modprobe mount umount fuser pgrep pkill killall npm node pip pip3 python perl ruby java make gcc cc
  ssh scp sftp telnet traceroute tracepath git curl wget openssl gpg ss ip awk sed grep find xargs diff cmp tar cat cp mv rm mkdir ln chmod chown mktemp stat df du
  head tail sort uniq cut tr wc ls seq tac tee date sleep basename dirname readlink base64 sha256sum nproc id uname env timeout install flock findmnt mountpoint
  free ps sysctl systemctl dpkg hostname python3 docker nginx journalctl ufw sudo pg_dump nvidia-smi"
missing_in_manifest() {
  local w
  cat "$ROOT/install.sh" "$ROOT"/scripts/*.sh "$ROOT"/scripts/lib/*.sh | awk '
    /^[[:space:]]*#/ {next}
    { line=$0; gsub(/\$/,"",line)
      n=split(line, parts, /(\|\||&&|\||;|\$\(|`|\()/)
      for(i=1;i<=n;i++){ p=parts[i]; sub(/^[[:space:]!{]+/,"",p); split(p, w, /[[:space:]]+/); c=w[1]
        j=1; while (c ~ /^[A-Za-z_][A-Za-z0-9_]*=/ && j<8) { j++; c=w[j] }
        while (c ~ /^(sudo|run|as_root|upd_as_root|env|exec|command|time|nohup|xargs|then|do|else|if|while|until|elif|!)$/ && j<12) { j++; c=w[j] }
        if (c ~ /^[a-z][a-z0-9_-]*$/) print c } }' | sort -u | while read -r w; do
    case " $KNOWN_UTILS " in *" $w "*) ;; *) continue ;; esac
    [ -n "${PREREQ_PKG[$w]:-}" ] && continue
    case " ${PREREQ_NOT_HOST[*]} nvidia-smi npm pg_dump mount umount " in *" $w "*) continue ;; esac   # mount/umount — слова из текстов сообщений, не вызовы
    echo "$w"
  done
}
t "манифест: все внешние программы скриптов перечислены (нет «сюрпризов» вроде buildx)" bash -c 'source "$1/scripts/lib/prereqlib.sh"; KNOWN_UTILS="$2"; '"$(declare -f missing_in_manifest)"'; ROOT="$1"; out="$(missing_in_manifest)"; [ -z "$out" ] || { echo "нет в манифесте: $out" >&2; exit 1; }' _ "$ROOT" "$KNOWN_UTILS"
t "манифест: каждая запись указывает на apt-пакет" bash -c 'source "$1/scripts/lib/prereqlib.sh"; for c in "${!PREREQ_PKG[@]}"; do [[ "${PREREQ_PKG[$c]}" =~ ^[a-z0-9][a-z0-9+.-]*$ ]] || exit 1; done' _ "$ROOT"
mkshim() { # mkshim КАТАЛОГ имя… — «чистая система»: в PATH только перечисленные базовые инструменты (обёртки), остального нет
  local d="$1" c p; shift; mkdir -p "$d"
  for c in "$@"; do p="$(type -P "$c")"; [ -n "$p" ] || continue; printf '#!/bin/sh
exec "%s" "$@"
' "$p" > "$d/$c"; chmod +x "$d/$c"; done
}
mkshim "$PQ/shim" sort tr grep id sed cat env bash dirname date mkdir cut head tail wc uname
t "манифест: docker и nginx — только standalone" bash -c 'source "$1/scripts/lib/prereqlib.sh"; PATH="$2/shim"; ! prereq_missing_bins shared-host | grep -qxE "docker|nginx"' _ "$ROOT" "$PQ"
t "манифест: Docker Buildx и Compose проверяются отдельно" bash -c 'source "$1/scripts/lib/prereqlib.sh"; mkdir -p "$2/bin2"; printf "#!/bin/sh\n[ \"\$1\" = compose ] && exit 0; exit 1\n" > "$2/bin2/docker"; chmod +x "$2/bin2/docker"; PATH="$2/bin2:$PATH"; [ "$(prereq_docker_missing)" = buildx ]' _ "$ROOT" "$PQ"
t "манифест: нет Docker — не хватает движка, Compose и Buildx" bash -c 'source "$1/scripts/lib/prereqlib.sh"; PATH="$2/shim"; [ "$(prereq_docker_missing | tr "\n" " ")" = "engine compose buildx " ]' _ "$ROOT" "$PQ"

# ---- 2. определение ОС
osr() { printf '%s\n' "$@" > "$PQ/os-release"; PREREQ_OS_RELEASE="$PQ/os-release"; }
chk_os() { # chk_os ожидаемое_supported ожидаемое_семейство строки os-release…
  local sup="$1" fam="$2"; shift 2
  ( osr "$@"; source "$ROOT/scripts/lib/prereqlib.sh"; prereq_os_detect >/dev/null 2>&1; [ "$PREREQ_SUPPORTED" = "$sup" ] && [ "$PREREQ_FAMILY" = "$fam" ] )
}
t "ОС: Ubuntu 24.04 поддерживается" chk_os yes ubuntu 'ID=ubuntu' 'ID_LIKE=debian' 'VERSION_ID="24.04"' 'VERSION_CODENAME=noble'
t "ОС: Debian 12 поддерживается" chk_os yes debian 'ID=debian' 'VERSION_ID="12"' 'VERSION_CODENAME=bookworm'
t "ОС: Debian 13 поддерживается" chk_os yes debian 'ID=debian' 'VERSION_ID="13"' 'VERSION_CODENAME=trixie'
t "ОС: Debian 11 слишком старый" chk_os no debian 'ID=debian' 'VERSION_ID="11"' 'VERSION_CODENAME=bullseye'
t "ОС: Ubuntu 20.04 слишком старая" chk_os no ubuntu 'ID=ubuntu' 'VERSION_ID="20.04"' 'VERSION_CODENAME=focal'
t "ОС: Alpine не поддерживается автоустановкой" chk_os no other 'ID=alpine' 'VERSION_ID="3.20"'
t "ОС: производная (Mint на Ubuntu) допускается" chk_os yes ubuntu 'ID=linuxmint' 'ID_LIKE="ubuntu debian"' 'VERSION_ID="22"' 'UBUNTU_CODENAME=jammy'
t "Docker-репозиторий выбирается по семейству ОС" bash -c 'source "$1/scripts/lib/prereqlib.sh"; PREREQ_FAMILY=ubuntu; [ "$(prereq_docker_repo_url)" = https://download.docker.com/linux/ubuntu ] && PREREQ_FAMILY=debian && [ "$(prereq_docker_repo_url)" = https://download.docker.com/linux/debian ]' _ "$ROOT"

# ---- 3. установка с «чистой» системы (dry-run, подставной apt-get): план содержит Docker Engine + Compose + Buildx, nginx и утилиты
printf '#!/bin/sh\nexit 0\n' > "$PQ/bin/apt-get"; chmod +x "$PQ/bin/apt-get"
plan() { ( osr "$@"; export PREREQ_OS_RELEASE; DRY_RUN=1; source "$ROOT/scripts/lib/common.sh"; PATH="$PQ/shim:$PQ/bin"; prereq_install_all standalone 2>&1 ) ; }
OUT_U="$(plan 'ID=ubuntu' 'VERSION_ID="24.04"' 'VERSION_CODENAME=noble')"
OUT_D="$(plan 'ID=debian' 'VERSION_ID="12"' 'VERSION_CODENAME=bookworm')"
t "чистая Ubuntu 24.04: план ставит git curl openssl nginx и утилиты" bash -c 'for w in git curl openssl nginx iproute2 util-linux gawk; do grep -qw -- "$w" <<<"$1" || exit 1; done' _ "$OUT_U"
t "чистая Ubuntu 24.04: Docker Engine, Compose и Buildx из репозитория Docker для ubuntu/noble" bash -c 'grep -q "docker-ce" <<<"$1" && grep -q "docker-buildx-plugin" <<<"$1" && grep -q "docker-compose-plugin" <<<"$1" && grep -q "linux/ubuntu noble" <<<"$1"' _ "$OUT_U"
t "чистый Debian 12: репозиторий Docker для debian/bookworm" bash -c 'grep -q "linux/debian bookworm" <<<"$1" && grep -q "docker-buildx-plugin" <<<"$1"' _ "$OUT_D"
t "установка не делает upgrade/dist-upgrade/autoremove/prune" bash -c '! grep -qE "upgrade|autoremove|prune" <<<"$1$2"' _ "$OUT_U" "$OUT_D"
t "apt не задаёт интерактивных вопросов (noninteractive, needrestart)" bash -c 'grep -q "DEBIAN_FRONTEND=noninteractive" <<<"$1" && grep -q "NEEDRESTART_MODE=a" <<<"$1"' _ "$OUT_U"
t "shared-host: Docker и nginx не ставятся" bash -c '! grep -qE "docker-ce|nginx" <<<"$1"' _ "$( ( osr 'ID=ubuntu' 'VERSION_ID="24.04"' 'VERSION_CODENAME=noble'; export PREREQ_OS_RELEASE; DRY_RUN=1; source "$ROOT/scripts/lib/common.sh"; PATH="$PQ/shim:$PQ/bin"; prereq_install_all shared-host 2>&1 ) )"
t "prereq.sh: --list и справка работают, неизвестный параметр отвергается" bash -c 'bash "$1/scripts/prereq.sh" --list | grep -q "^docker " && ! bash "$1/scripts/prereq.sh" --nonsense >/dev/null 2>&1' _ "$ROOT"

# ---- 4. стерильное окружение: .env — единственный источник настроек
printf 'COMPOSE_PROJECT_NAME=pg-sterile\nASR_MAX_CONCURRENT_INFERENCE=1\nASR_CPU_THREADS=6\nDATA_ROOT=/srv/x\n' > "$PQ/s.env"
sterile() { env -i PATH="$PATH" HOME="$TMP" ENV_FILE="$PQ/s.env" "$@" bash -c 'source "$1/scripts/lib/common.sh"; sanitize_project_env; load_env "$ENV_FILE"; printf "%s/%s/%s/%s" "$ASR_MAX_CONCURRENT_INFERENCE" "$ASR_CPU_THREADS" "${NGINX_LISTEN_PORT:-пусто}" "${LIVEKIT_NODE_IP:-пусто}"' _ "$ROOT"; }
t "окружение: унаследованный ASR_MAX_CONCURRENT_INFERENCE=2 не перебивает .env (=1)" eq "$(sterile ASR_MAX_CONCURRENT_INFERENCE=2 ASR_CPU_THREADS=12)" "1/6/пусто/пусто"
t "окружение: посторонние NGINX_* и LIVEKIT_* не попадают в конфигурацию" eq "$(sterile NGINX_LISTEN_PORT=9999 LIVEKIT_NODE_IP=203.0.113.9)" "1/6/пусто/пусто"
t "окружение: PEREGOVORKA_KEEP_ENV=all оставляет всё как есть (для отладки)" eq "$(sterile PEREGOVORKA_KEEP_ENV=all ASR_CPU_THREADS=12)" "1/12/пусто/пусто"
t "окружение: управляющие переменные (DRY_RUN, ENV_FILE) сохраняются" bash -c 'out="$(env -i PATH="$PATH" ENV_FILE="$2/s.env" DRY_RUN=1 bash -c "source \"\$1/scripts/lib/common.sh\"; sanitize_project_env; printf %s \"\$DRY_RUN\"" _ "$1")"; [ "$out" = 1 ]' _ "$ROOT" "$PQ"
t "окружение: все жизненные скрипты вызывают sanitize_project_env перед load_env" bash -c 'for s in install update updater rebuild ctl rollback verify status smoke-test backup; do grep -q "sanitize_project_env; load_env" "$1/scripts/$s.sh" || { echo "$s"; exit 1; }; done' _ "$ROOT"

# ---- 5. белый список исправлений
t "исправления: неизвестный ID отвергается, известные принимаются" bash -c 'source "$1/scripts/lib/repairlib.sh"; ! repair_id_valid "rm -rf /" && ! repair_id_valid "" && ! repair_id_valid "../update" && repair_id_valid data_dirs && repair_id_valid nginx_site && repair_id_valid sysctl' _ "$ROOT"
t "исправления: repair_apply с неизвестным ID ничего не выполняет" bash -c 'source "$1/scripts/lib/common.sh"; ! repair_apply "x; touch $2/pwned" >/dev/null 2>&1 && [ ! -e "$2/pwned" ]' _ "$ROOT" "$PQ"
t "исправления: у каждой проблемы есть описание из трёх частей без команд Linux" bash -c 'source "$1/scripts/lib/repairlib.sh"; for i in "${REPAIR_IDS[@]}"; do t="$(repair_text "$i")"; [ "$(tr -cd "|" <<<"$t" | wc -c)" -ge 2 ] || exit 1; ! grep -qE "sudo|chown|chmod|sysctl|nginx -t|docker compose" <<<"$t" || exit 2; done' _ "$ROOT"
mkdir -p "$PQ/data/updater" "$PQ/data/state"
t "исправления: каталог данных без нужного владельца/режима обнаруживается" bash -c 'source "$1/scripts/lib/common.sh"; DATA_ROOT="$2/data"; repair_detect_data_dirs && grep -q "recordings" <<<"$REPAIR_DETAIL"' _ "$ROOT" "$PQ"
t "исправления: repairs.json — корректный JSON с тремя фразами" bash -c 'source "$1/scripts/lib/common.sh"; DATA_ROOT="$2/data"; INSTALL_PROFILE=shared-host; repair_scan; repair_json > "$2/repairs.json"; grep -q "\"items\":\[" "$2/repairs.json" && grep -q "\"meaning\"" "$2/repairs.json"; if [ -n "$3" ]; then "$3" -c "import json,sys; d=json.load(open(sys.argv[1], encoding=\"utf-8\")); assert d[\"items\"][0][\"fix\"]" "$2/repairs.json"; fi' _ "$ROOT" "$PQ" "${PYJ:-}"
t "repair.sh: --list печатает белый список, неизвестный ID и произвольная команда отвергаются" bash -c 'bash "$1/scripts/repair.sh" --list | grep -qx data_dirs && ! bash "$1/scripts/repair.sh" "rm -rf /" --env "$2/s.env" >/dev/null 2>&1 && ! bash "$1/scripts/repair.sh" nonsense --env "$2/s.env" >/dev/null 2>&1' _ "$ROOT" "$PQ"
