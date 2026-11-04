#!/bin/bash
# install.sh — установка и обновление узла CDN Peregovorka (Ubuntu 22.04/24.04/26.04, Debian 12/13; amd64, arm64). Запуск от root.
#   install.sh --facts                 только сведения о сервере (JSON) — предварительная проверка, ничего не меняет
#   install.sh --config node.json      установка или повторная установка с этими настройками (повторный запуск безопасен)
#   install.sh --upgrade               обновить nginx/njs и файлы узла, настройки — прежние (/etc/pgcdn/node.json)
# Ход выполнения — строки "@@PGCDN <этап> <start|ok|fail|warn> <текст>": их читает основной сервер и показывает в администрировании.
# Пакеты nginx и njs — из официального репозитория nginx.org (ключ проверяется по отпечатку). Ничего, кроме описанного здесь, не меняется:
# DNS, сетевые правила и учётные записи администраторов сервера не трогаются; правило ufw добавляется, только если ufw уже включён.
set -uo pipefail
umask 022
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETC=/etc/pgcdn
LIB=/var/lib/pgcdn
CACHE=/var/cache/pgcdn
LOCK=/run/pgcdn-install.lock
NGINX_FPRS="573BFD6B3D8FBC641079A6ABABF5BD827BD9BF62 8540A6F18833A80E9C1653A42FD21310B49F6B46 9E9BE90EACBCDE69FE9B204CBCDCD8A38D88A2B3"
export DEBIAN_FRONTEND=noninteractive

say() { echo "@@PGCDN $1 $2 ${3:-}"; }
fail() { say "$1" fail "$2"; exit 1; }
apt_q() { apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=120 -y -q "$@" >/tmp/pgcdn-apt.log 2>&1; }
in_container() { systemd-detect-virt -cq 2>/dev/null; }

facts() {
    . /etc/os-release
    local disks fs links nic=() n s
    disks=$(lsblk -J -b -d -o NAME,SIZE,ROTA,TYPE,MODEL 2>/dev/null | tr -d '\n' || true); [ -n "$disks" ] || disks='{"blockdevices":[]}'
    fs=$(df -B1 --output=target,fstype,size,avail -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null | tail -n +2 | \
         awk '{printf "%s{\"mount\":\"%s\",\"type\":\"%s\",\"size\":%s,\"avail\":%s}", (NR>1?",":""), $1,$2,$3,$4}')
    for n in /sys/class/net/*; do
        [ "$(basename "$n")" = lo ] && continue
        s=$(cat "$n/speed" 2>/dev/null || echo -1); [[ "$s" =~ ^-?[0-9]+$ ]] || s=-1
        nic+=("{\"name\":\"$(basename "$n")\",\"speed_mbit\":$s}")
    done
    links=$(IFS=,; echo "${nic[*]:-}")
    local sudo_ok=false; [ "$(id -u)" = 0 ] && sudo_ok=true
    local cache_fs_avail; cache_fs_avail=$(df -B1 --output=avail /var/cache 2>/dev/null | tail -1 | tr -d ' ')
    local nginx_present=false; command -v nginx >/dev/null && nginx_present=true
    local installed=""; [ -r $ETC/VERSION ] && installed=$(cat $ETC/VERSION)
    local distro_nginx=false; dpkg -s nginx-common >/dev/null 2>&1 && distro_nginx=true
    local p80 p443
    p80=$(ss -Hltn 'sport = :80' 2>/dev/null | grep -c . || true); p443=$(ss -Hltn 'sport = :443' 2>/dev/null | grep -c . || true)
    printf '{"os_id":"%s","os_version":"%s","os_name":"%s","codename":"%s","arch":"%s","cpus":%s,"cpu_model":"%s","mem_bytes":%s,' \
        "${ID:-}" "${VERSION_ID:-}" "${PRETTY_NAME:-}" "${VERSION_CODENAME:-}" "$(dpkg --print-architecture 2>/dev/null || uname -m)" "$(nproc)" \
        "$(awk -F': ' '/model name|^Model/ {print $2; exit}' /proc/cpuinfo | tr -d '"\\' | head -c 120)" "$(awk '/MemTotal/ {print $2*1024}' /proc/meminfo)"
    printf '"disks":%s,"filesystems":[%s],"nics":[%s],"root":%s,"systemd":%s,"container":%s,"cache_fs_avail":%s,"nginx_present":%s,"installed":"%s",' \
        "$disks" "$fs" "$links" "$sudo_ok" "$( [ -d /run/systemd/system ] && echo true || echo false)" "$(in_container && echo true || echo false)" \
        "${cache_fs_avail:-0}" "$nginx_present" "$installed"
    printf '"ports_busy":{"80":%s,"443":%s},"distro_nginx":%s,"kernel":"%s"}\n' "${p80:-0}" "${p443:-0}" "$distro_nginx" "$(uname -r)"
}

check_os() {
    . /etc/os-release
    case "${ID:-}:${VERSION_ID:-}" in
        ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;;
        *) fail check "система ${PRETTY_NAME:-неизвестна} не поддерживается: нужны Ubuntu 22.04/24.04/26.04 или Debian 12/13" ;;
    esac
    case "$(dpkg --print-architecture)" in amd64|arm64) ;; *) fail check "архитектура $(dpkg --print-architecture) не поддерживается (amd64, arm64)" ;; esac
    [ -d /run/systemd/system ] || fail check "нужен systemd"
    [ "$(id -u)" = 0 ] || fail check "нужен root (sudo)"
}

packages() {
    say packages start "пакеты nginx и njs из nginx.org"
    . /etc/os-release
    apt_q update || fail packages "apt-get update: $(tail -c 300 /tmp/pgcdn-apt.log)"
    apt_q install --no-install-recommends ca-certificates curl gnupg jq openssl || fail packages "базовые пакеты: $(tail -c 300 /tmp/pgcdn-apt.log)"
    local kr=/usr/share/keyrings/nginx-archive-keyring.gpg tmp
    tmp=$(mktemp)
    curl -fsSL --retry 3 https://nginx.org/keys/nginx_signing.key -o "$tmp" || fail packages "ключ nginx.org не скачался"
    local fprs ok=0 f
    fprs=$(gpg --show-keys --with-colons "$tmp" 2>/dev/null | awk -F: '/^fpr/ {print $10}')
    for f in $fprs; do case " $NGINX_FPRS " in *" $f "*) ok=1 ;; *) rm -f "$tmp"; fail packages "ключ nginx.org с неизвестным отпечатком $f" ;; esac; done
    [ "$ok" = 1 ] || fail packages "в ключе nginx.org нет отпечатков"
    gpg --dearmor < "$tmp" > "$kr.new" && mv -f "$kr.new" "$kr"; rm -f "$tmp"
    echo "deb [signed-by=$kr] https://nginx.org/packages/$ID $VERSION_CODENAME nginx" > /etc/apt/sources.list.d/nginx.list
    printf 'Package: *\nPin: origin nginx.org\nPin: release o=nginx\nPin-Priority: 900\n' > /etc/apt/preferences.d/99nginx
    apt_q update || fail packages "apt-get update (nginx.org): $(tail -c 300 /tmp/pgcdn-apt.log)"
    if dpkg -s nginx-common >/dev/null 2>&1; then
        fail packages "установлен nginx из пакетов дистрибутива (nginx-common): узлу нужен nginx.org — удалите прежний nginx или используйте отдельный сервер"
    fi
    apt_q install nginx nginx-module-njs || fail packages "установка nginx: $(tail -c 300 /tmp/pgcdn-apt.log)"
    [ "${TLS_MODE:-}" = acme ] && { apt_q install --no-install-recommends certbot || fail packages "certbot: $(tail -c 300 /tmp/pgcdn-apt.log)"; }
    say packages ok "$(nginx -v 2>&1 | sed 's#.*/##'), njs $(dpkg-query -W -f='${Version}' nginx-module-njs | sed 's/.*+//; s/-.*//')"
}

files() {
    say files start "файлы узла"
    install -d -m 0755 $ETC $ETC/tls $LIB $LIB/acme
    install -d -m 0750 -o nginx -g nginx $CACHE
    install -m 0644 "$HERE/pgcdn.js" $ETC/pgcdn.js
    install -m 0644 "$HERE/nginx.conf.tpl" $ETC/nginx.conf.tpl
    install -m 0755 "$HERE/pgcdn-ctl" /usr/local/sbin/pgcdn-ctl
    install -m 0755 "$HERE/pgcdn-ssh" /usr/local/sbin/pgcdn-ssh
    install -m 0644 "$HERE/VERSION" $ETC/VERSION
    cat > /etc/systemd/system/pgcdn-disk.service <<'EOF'
[Unit]
Description=Peregovorka CDN: объём кеша для статистики
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/pgcdn-ctl diskstat
Nice=10
IOSchedulingClass=idle
EOF
    cat > /etc/systemd/system/pgcdn-disk.timer <<'EOF'
[Unit]
Description=Peregovorka CDN: объём кеша раз в минуту
[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
[Install]
WantedBy=timers.target
EOF
    install -d /etc/systemd/system/nginx.service.d
    printf '[Service]\nLimitNOFILE=262144\nRestart=on-failure\nRestartSec=2s\n' > /etc/systemd/system/nginx.service.d/pgcdn.conf
    systemctl daemon-reload
    say files ok ""
}

account() {
    # Учётная запись управления: только ключ основного сервера, принудительная команда pgcdn-ssh, sudo — только для pgcdn-ctl.
    say account start "учётная запись управления pgcdn"
    local pub
    pub=$(jq -r '.mgmt_pubkey // empty' "$CFG")
    [[ "$pub" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+( [^\"]*)?$ ]] || fail account "в настройках нет ключа управления ssh-ed25519"
    id pgcdn >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/pgcdn-home --shell /bin/sh pgcdn || fail account "useradd pgcdn"
    usermod -s /bin/sh pgcdn
    install -d -m 0700 -o pgcdn -g pgcdn /var/lib/pgcdn-home/.ssh
    printf 'command="/usr/local/sbin/pgcdn-ssh",restrict %s\n' "$pub" > /var/lib/pgcdn-home/.ssh/authorized_keys
    chown pgcdn:pgcdn /var/lib/pgcdn-home/.ssh/authorized_keys; chmod 0600 /var/lib/pgcdn-home/.ssh/authorized_keys
    passwd -l pgcdn >/dev/null 2>&1
    echo 'pgcdn ALL=(root) NOPASSWD: /usr/local/sbin/pgcdn-ctl' > /etc/sudoers.d/pgcdn.new
    chmod 0440 /etc/sudoers.d/pgcdn.new
    visudo -cf /etc/sudoers.d/pgcdn.new >/dev/null || { rm -f /etc/sudoers.d/pgcdn.new; fail account "правило sudo не прошло проверку visudo"; }
    mv -f /etc/sudoers.d/pgcdn.new /etc/sudoers.d/pgcdn
    say account ok ""
}

tls() {
    say tls start "сертификат TLS (${TLS_MODE})"
    local name; name=$(jq -r .public_name "$CFG")
    case "$TLS_MODE" in
        files)
            jq -r '.tls_cert_pem // empty' "$CFG" > $ETC/tls/node.crt.new
            ( umask 077; jq -r '.tls_key_pem // empty' "$CFG" > $ETC/tls/node.key.new )
            openssl x509 -noout -in $ETC/tls/node.crt.new 2>/dev/null || fail tls "сертификат не читается (нужен PEM)"
            openssl pkey -noout -in $ETC/tls/node.key.new 2>/dev/null || fail tls "закрытый ключ не читается (нужен PEM)"
            [ "$(openssl x509 -noout -pubkey -in $ETC/tls/node.crt.new | openssl sha256)" = "$(openssl pkey -pubout -in $ETC/tls/node.key.new | openssl sha256)" ] || fail tls "ключ не подходит к сертификату"
            mv -f $ETC/tls/node.crt.new $ETC/tls/node.crt; mv -f $ETC/tls/node.key.new $ETC/tls/node.key
            jq 'del(.tls_cert_pem, .tls_key_pem) | .tls_cert_path="/etc/pgcdn/tls/node.crt" | .tls_key_path="/etc/pgcdn/tls/node.key"' "$CFG" > "$CFG.n" && mv -f "$CFG.n" "$CFG" ;;
        selfsigned|acme)
            if [ ! -s $ETC/tls/node.crt ] || ! openssl x509 -noout -checkend 86400 -in $ETC/tls/node.crt >/dev/null 2>&1; then
                ( umask 077; openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 -subj "/CN=$name" \
                    -addext "subjectAltName=DNS:$name" -keyout $ETC/tls/node.key -out $ETC/tls/node.crt >/dev/null 2>&1 ) || fail tls "openssl: самоподписанный сертификат"
            fi
            jq '.tls_cert_path="/etc/pgcdn/tls/node.crt" | .tls_key_path="/etc/pgcdn/tls/node.key"' "$CFG" > "$CFG.n" && mv -f "$CFG.n" "$CFG" ;;
        *) fail tls "неизвестный режим TLS: $TLS_MODE" ;;
    esac
    chmod 0600 $ETC/tls/node.key
    say tls ok ""
}

acme() {
    # Let's Encrypt (HTTP-01): имя узла должно указывать на этот сервер, порт 80 — открыт из интернета. nginx уже запущен с временным сертификатом.
    [ "$TLS_MODE" = acme ] || return 0
    say acme start "Let's Encrypt"
    local name email args
    name=$(jq -r .public_name "$CFG"); email=$(jq -r '.acme_email // empty' "$CFG")
    args=(--register-unsafely-without-email); [ -n "$email" ] && args=(-m "$email")
    certbot certonly --webroot -w $LIB/acme -d "$name" --non-interactive --agree-tos "${args[@]}" --keep-until-expiring \
        --deploy-hook "systemctl reload nginx" >/tmp/pgcdn-acme.log 2>&1 || fail acme "certbot: $(tail -c 400 /tmp/pgcdn-acme.log)"
    jq --arg c "/etc/letsencrypt/live/$name/fullchain.pem" --arg k "/etc/letsencrypt/live/$name/privkey.pem" '.tls_cert_path=$c | .tls_key_path=$k' "$CFG" > "$CFG.n" && mv -f "$CFG.n" "$CFG"
    /usr/local/sbin/pgcdn-ctl apply < "$CFG" >/dev/null || fail acme "nginx не принял сертификат Let's Encrypt"
    say acme ok "сертификат выпущен, продление — таймер certbot"
}

system() {
    say system start "параметры системы"
    if in_container; then
        say system warn "контейнер: параметры ядра не меняются (их задаёт хост)"
    else
        cat > /etc/sysctl.d/90-pgcdn.conf <<'EOF'
# Peregovorka CDN: много одновременных соединений зрителей
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 10240 65000
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_slow_start_after_idle = 0
net.core.netdev_max_backlog = 16384
fs.file-max = 2097152
EOF
        sysctl -q -p /etc/sysctl.d/90-pgcdn.conf >/dev/null 2>&1 || say system warn "часть параметров ядра не применилась"
    fi
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && say system warn "ufw: открыты 80 и 443/tcp"
    fi
    say system ok ""
}

start() {
    say start start "запуск nginx"
    systemctl stop nginx 2>/dev/null
    /usr/local/sbin/pgcdn-ctl apply < "$CFG" >/tmp/pgcdn-apply.log 2>&1 || fail start "$(tail -c 500 /tmp/pgcdn-apply.log)"
    systemctl enable -q nginx && systemctl enable -q --now pgcdn-disk.timer
    /usr/local/sbin/pgcdn-ctl diskstat
    systemctl is-active -q nginx || fail start "nginx не запустился: $(journalctl -u nginx -n 20 --no-pager 2>/dev/null | tail -c 400)"
    say start ok ""
}

selfcheck() {
    say selfcheck start "проверка узла"
    local name origin key code
    name=$(jq -r .public_name "$CFG"); origin=$(jq -r .origin_url "$CFG"); key=$(jq -r .pull_key "$CFG")
    code=$(curl -sk -o /dev/null -w '%{http_code}' --resolve "$name:443:127.0.0.1" "https://$name/.pgcdn/healthz") || true
    [ "$code" = 200 ] || fail selfcheck "узел не отвечает на 443 (код $code)"
    local k=() ; [ "$(jq -r '.origin_tls_verify' "$CFG")" = false ] && k=(-k)
    code=$(curl -s "${k[@]}" -o /dev/null -w '%{http_code}' -m 10 -H "X-Pgcdn-Key: $key" "$origin/cdn-origin/_ping") || true
    case "$code" in
        200) say selfcheck ok "источник принимает узел" ;;
        403) fail selfcheck "источник отклонил ключ узла (403): узел ещё не зарегистрирован на основном сервере" ;;
        000) fail selfcheck "источник $origin недоступен с узла" ;;
        *) fail selfcheck "источник ответил $code на /cdn-origin/_ping" ;;
    esac
}

[ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null && { say lock fail "установка уже идёт (pid $(cat "$LOCK"))"; exit 1; }
echo $$ > "$LOCK"; trap 'rm -f "$LOCK"' EXIT

case "${1:-}" in
    --facts) facts ;;
    --config)
        [ -r "${2:-}" ] || fail check "нет файла настроек"
        check_os
        CFG=$(mktemp); install -m 0600 "$2" "$CFG"; trap 'rm -f "$LOCK" "$CFG" "$CFG.n"' EXIT
        TLS_MODE=$(jq -r '.tls_mode // "selfsigned"' "$CFG" 2>/dev/null || echo selfsigned)
        say check ok "$(. /etc/os-release; echo "$PRETTY_NAME"), $(dpkg --print-architecture)"
        packages; files; account; tls; system
        jq 'del(.mgmt_pubkey, .tls_cert_pem, .tls_key_pem)' "$CFG" > "$CFG.n" && mv -f "$CFG.n" "$CFG"
        start; acme
        install -m 0600 "$CFG" $ETC/node.json.installed
        selfcheck
        say done ok "$(cat $ETC/VERSION)" ;;
    --upgrade)
        check_os
        [ -s $ETC/node.json ] || fail check "узел не установлен"
        CFG=$ETC/node.json; TLS_MODE=$(jq -r '.tls_mode // "selfsigned"' "$CFG")
        say check ok "обновление"
        packages; files
        /usr/local/sbin/pgcdn-ctl render >/tmp/pgcdn-apply.log 2>&1 || fail start "$(tail -c 500 /tmp/pgcdn-apply.log)"
        systemctl restart nginx || fail start "nginx не перезапустился"
        say done ok "$(cat $ETC/VERSION)" ;;
    *) echo "install.sh --facts | --config node.json | --upgrade" >&2; exit 2 ;;
esac
