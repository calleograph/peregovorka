#!/usr/bin/env bash
# apt-e2e.sh — испытание репозитория APT в чистой системе (контейнер, сеть хоста), от установки до удаления:
#   desktop/linux/test/apt-e2e.sh <образ> <сайт-v1> <сайт-v2> <версия-v1> <версия-v2> [порт]
# сайт-v1 — подписанный репозиторий с первой версией, сайт-v2 — с обеими (make-repo.sh + sign-repo.sh). Проверяется:
#  установка install.sh (ключ по отпечатку, signed-by) → подмена индекса без подписи и подпись чужим ключом отвергаются → репозиторий недоступен → обновление
#  apt update/upgrade с оборванной первой загрузкой пакета → откат на прежнюю версию из истории репозитория → unattended-upgrades (по выбору
#  администратора) видит обновление → apt purge убирает репозиторий.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="${1:?образ}"; S1="$(cd "${2:?}" && pwd)"; S2="$(cd "${3:?}" && pwd)"; V1="${4:?}"; V2="${5:?}"; PORT="${6:-18765}"
W="$(mktemp -d)"; C="pg-apt-e2e-$$"
cleanup() { docker rm -f "$C" >/dev/null 2>&1 || true; [ -n "${SRV:-}" ] && kill "$SRV" 2>/dev/null || true; rm -rf "$W"; }
trap cleanup EXIT
fail() { echo "ОШИБКА: $*" >&2; exit 1; }
ok() { echo "OK   $*"; }
serve() { rm -f "$W/serve"; ln -s "$1" "$W/serve"; }
X() { docker exec "$C" bash -c "$*"; }
ver() { X "dpkg-query -W -f '\${Version}' peregovorka 2>/dev/null" || true; }

ss -ltn | awk '{print $4}' | grep -q ":$PORT\$" && fail "порт $PORT занят другой программой — укажите свободный"
mkdir -p "$W/root"; serve "$S1"
python3 "$HERE/faultserver.py" "$W/serve" "$PORT" > "$W/server.log" 2>&1 & SRV=$!
for i in $(seq 30); do curl -sf "http://127.0.0.1:$PORT/dists/stable/InRelease" >/dev/null && break; sleep 0.3; done
docker run -d --name "$C" --network host "$IMG" sleep infinity >/dev/null
docker cp "$HERE/../install.sh" "$C:/install.sh"
X ". /etc/os-release; echo === \$PRETTY_NAME"

# 1) установка установочным скриптом
X "PEREGOVORKA_APT_URL=http://127.0.0.1:$PORT PEREGOVORKA_APT_INSECURE_TEST=1 sh /install.sh" > "$W/install.log" 2>&1 || { tail -20 "$W/install.log"; fail "install.sh"; }
[ "$(ver)" = "$V1" ] || fail "после install.sh версия $(ver), ожидалась $V1"
X "apt-cache policy peregovorka" | grep -q "o=Peregovorka" || X "apt-cache policy peregovorka" | grep -q "127.0.0.1:$PORT" || fail "пакет не из репозитория Peregovorka"
X "grep -q '^Signed-By: /usr/share/keyrings/peregovorka-archive-keyring.gpg' /etc/apt/sources.list.d/peregovorka.sources" || fail "источник без signed-by"
X "ls /etc/apt/sources.list.d/ | grep -c peregovorka" | grep -qx 1 || fail "источников Peregovorka больше одного"
ok "install.sh: ключ сверен по отпечатку, источник signed-by, установлена $V1 из репозитория"

# 2) подмена индекса без подписи и чужая подпись — apt отказывается, установленная версия не меняется
cp -r "$S2" "$W/tamper"; printf '\n' >> "$W/tamper/dists/stable/main/binary-amd64/Packages"; rm -f "$W/tamper"/dists/stable/main/binary-amd64/Packages.{gz,xz}
serve "$W/tamper"
X "apt-get update 2>&1" > "$W/tamper.log" || true
grep -Eqi "hash sum mismatch|unexpected size|Mirror sync in progress|File has unexpected" "$W/tamper.log" || { cat "$W/tamper.log"; fail "подменённый индекс не отвергнут"; }
X "apt-get -s upgrade" | grep -q "^Inst peregovorka" && fail "после подмены индекса apt предлагает установить пакет"
ok "индекс, изменённый без подписи, отвергнут (Hash Sum mismatch)"
cp -r "$S2" "$W/wrongkey"; export GNUPGHOME="$W/gnupg"; mkdir -m 700 "$GNUPGHOME"
gpg --batch --quiet --passphrase '' --quick-gen-key "Чужой ключ <x@example.org>" rsa3072 sign 1d 2>/dev/null
rm -f "$W/wrongkey/dists/stable/InRelease" "$W/wrongkey/dists/stable/Release.gpg"
gpg --batch --quiet --clearsign -o "$W/wrongkey/dists/stable/InRelease" "$W/wrongkey/dists/stable/Release"
gpg --batch --quiet -abs -o "$W/wrongkey/dists/stable/Release.gpg" "$W/wrongkey/dists/stable/Release"; unset GNUPGHOME
serve "$W/wrongkey"
X "apt-get update 2>&1" > "$W/wrongkey.log" || true
grep -Eqi "NO_PUBKEY|not signed|signatures couldn't be verified|is not signed|Missing key" "$W/wrongkey.log" || { cat "$W/wrongkey.log"; fail "подпись чужим ключом не отвергнута"; }
X "apt-get -s upgrade" | grep -q "^Inst peregovorka" && fail "после чужой подписи apt предлагает установить пакет"
ok "подпись чужим ключом отвергнута (NO_PUBKEY)"

# 2б) репозиторий недоступен (сайт не отвечает: 404 на всё) — apt update сообщает об ошибке, установленная программа и dpkg не затронуты
serve "$W/nonexistent"
X "apt-get update 2>&1" > "$W/down.log" && fail "apt update при недоступном репозитории завершился без ошибки"
grep -Eqi "404|does not have a Release file|Failed to fetch" "$W/down.log" || { cat "$W/down.log"; fail "неожиданная реакция apt на недоступный репозиторий"; }
[ "$(ver)" = "$V1" ] || fail "при недоступном репозитории версия изменилась: $(ver)"
X "dpkg --audit" | grep -q . && fail "dpkg --audit при недоступном репозитории"
X "dpkg -V peregovorka" | grep -q . && fail "файлы программы изменены"
X "apt-get -s upgrade" | grep -q "^Inst peregovorka" && fail "при недоступном репозитории apt предлагает установку"
ok "репозиторий недоступен: apt update сообщает об ошибке, установленная $V1 цела (dpkg -V), dpkg в порядке"

# 3) обновление через apt update && apt upgrade, первая загрузка пакета обрывается на середине
serve "$S2"; touch "$S2/.truncate-once"
X "apt-get update -qq" || fail "apt update на настоящем репозитории"
X "apt-get upgrade -y 2>&1" > "$W/upgrade1.log" || true
rm -f "$S2/.truncate-once"
if [ "$(ver)" != "$V2" ]; then
  grep -Eqi "hash sum mismatch|size mismatch|Connection|failed to fetch|unexpected" "$W/upgrade1.log" || { cat "$W/upgrade1.log"; fail "неожиданная ошибка обновления"; }
  [ "$(ver)" = "$V1" ] || fail "после прерванной загрузки версия $(ver) — ни прежняя, ни новая"
  X "dpkg --audit" | grep -q . && fail "после прерванной загрузки dpkg в несогласованном состоянии"
  ok "прерванная загрузка: apt отказался ставить неполный файл, установлена прежняя $V1, dpkg в порядке"
  X "apt-get upgrade -y" > "$W/upgrade2.log" 2>&1 || { tail -20 "$W/upgrade2.log"; fail "повторное apt upgrade"; }
else
  grep -q "оборвана загрузка" "$W/server.log" || fail "загрузка не обрывалась"
  ok "прерванная загрузка: apt повторил загрузку сам (Acquire::Retries)"
fi
[ "$(ver)" = "$V2" ] || fail "после apt upgrade версия $(ver), ожидалась $V2"
X "dpkg --audit" | grep -q . && fail "dpkg --audit"
ok "apt update && apt upgrade: $V1 → $V2"

# 4) восстановление после неудачного обновления: прежняя версия из истории репозитория, затем снова новая
X "apt-get install -y -qq --allow-downgrades peregovorka=$V1 >/dev/null" || fail "откат на $V1"
[ "$(ver)" = "$V1" ] || fail "откат не выполнен"
X "apt-get install -y -qq peregovorka >/dev/null" && [ "$(ver)" = "$V2" ] || fail "возврат к $V2"
ok "откат на $V1 (apt install peregovorka=$V1 --allow-downgrades) и возврат к $V2"

# 5) unattended-upgrades: обновления Peregovorka ставятся автоматически, только если администратор добавил источник в разрешённые
X "apt-get install -y -qq --allow-downgrades peregovorka=$V1 >/dev/null; apt-get install -y -qq --no-install-recommends unattended-upgrades python3 >/dev/null 2>&1"
X "unattended-upgrade --dry-run -v 2>&1" > "$W/uu-default.log" || true
grep -q "peregovorka" "$W/uu-default.log" && grep -Eq "Packages that (will|would) be upgraded:.*peregovorka" "$W/uu-default.log" && fail "unattended-upgrades без настройки обновил бы Peregovorka"
X "printf 'Unattended-Upgrade::Origins-Pattern:: \"origin=Peregovorka,suite=stable\";\n' > /etc/apt/apt.conf.d/52peregovorka-unattended"
X "unattended-upgrade -v 2>&1" > "$W/uu.log" || true
[ "$(ver)" = "$V2" ] || { tail -30 "$W/uu.log"; fail "unattended-upgrades с разрешённым источником не обновил ($(ver))"; }
ok "unattended-upgrades: без настройки не трогает, с Origins-Pattern origin=Peregovorka обновил $V1 → $V2"

# 6) удаление программы и репозитория
X "apt-get purge -y -qq peregovorka >/dev/null" || fail "apt purge"
X "test ! -e /etc/apt/sources.list.d/peregovorka.sources && test ! -e /usr/share/keyrings/peregovorka-archive-keyring.gpg" || fail "purge оставил репозиторий"
X "apt-get update -qq" || fail "apt update после purge"
ok "apt purge: программа, репозиторий и ключ удалены, apt update работает"
echo "ИТОГО: репозиторий прошёл все проверки на $IMG"
