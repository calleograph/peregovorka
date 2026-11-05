#!/bin/bash
# Внешние тома на настоящем ядре: ВМ Ubuntu, настоящий сервер NFS, Docker, распространение монтирований, служба помощника. На общем сервере не запускать: нужен root и mount.
#   sudo tests/live/volumes/vm_e2e.sh prep     установка пакетов, экспорт NFS, контейнер приложения
#   sudo tests/live/volumes/vm_e2e.sh main     подключение, запись, потеря и возврат тома, зависший сервер
#   sudo tests/live/volumes/vm_e2e.sh reboot   после перезагрузки ВМ: том вернулся сам
# Контейнер с uid 10001 подключён к /data/ext так же, как backend в deployment/compose.yml.
set -u
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"; D=/srv/pgdata; MNT=/mnt/qnap-nfs; EXP=/srv/export
PASS=0; FAIL=0
t() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then PASS=$((PASS+1)); echo "ok   $d"; else FAIL=$((FAIL+1)); echo "FAIL $d"; fi; }
not() { ! "$@"; }
vol() { "$R/scripts/volumes.sh" "$@" --env "$R/.env"; }
in_app() { docker exec app sh -c "$1"; }
ID() { python3 - <<PY
import hashlib,re
p="$MNT"; print(re.sub(r"[^a-z0-9]+","-",p.lstrip("/").lower()).strip("-")[:40]+"-"+hashlib.sha256(p.encode()).hexdigest()[:6])
PY
}
wait_for() { local i; for i in $(seq 1 "${2:-30}"); do eval "$1" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }

case "${1:-}" in
prep)
  apt-get -qq update && apt-get -qq install -y docker.io nfs-kernel-server nfs-common jq >/dev/null
  mkdir -p "$EXP" "$MNT" "$D/state" "$D/updater"; chmod 1777 "$D/updater"; chown 10001:10001 "$EXP"
  grep -q "$EXP" /etc/exports || echo "$EXP 127.0.0.1(rw,sync,no_subtree_check)" >> /etc/exports
  exportfs -ra; systemctl enable --now nfs-server
  grep -q qnap-nfs /etc/fstab || echo "127.0.0.1:$EXP $MNT nfs4 _netdev,nofail,soft,timeo=30,retrans=2 0 0" >> /etc/fstab
  mount "$MNT"
  printf 'COMPOSE_PROJECT_NAME=pgvol\nDATA_ROOT=%s\n' "$D" > "$R/.env"
  mkdir -p "$D/ext"
  docker pull -q python:3.12-alpine
  docker rm -f app >/dev/null 2>&1
  docker run -d --name app --restart unless-stopped -u 10001:10001 --mount "type=bind,src=$D/ext,dst=/data/ext,bind-propagation=rslave" python:3.12-alpine sleep infinity
  docker cp "$R/backend/app/services/mounts.py" app:/tmp/mounts.py
  ;;
main)
  ID_="$(ID)"; C="/data/ext/$ID_"
  t "том на сервере смонтирован по NFS" findmnt -t nfs4 "$MNT"
  t "до подключения контейнер его не видит" not in_app "test -d $C"
  t "путь вне разрешённых отклоняется" not vol attach /etc
  t "подключение" vol attach "$MNT"
  t "работающий контейнер видит том без перезапуска" in_app "test -d $C"
  t "в контейнере настоящая файловая система NFS" bash -c "docker exec app grep -q ' $C .* nfs4 ' /proc/self/mountinfo"
  t "запись от uid 10001" in_app "echo first > $C/a.txt"
  t "файл на сервере хранения" grep -q first "$EXP/a.txt"
  docker exec app python3 -c "import sys; sys.path.insert(0,'/tmp'); import mounts; d=mounts.describe('$C'); print(d); sys.exit(0 if d['fstype']=='nfs4' and d['mountpoint']=='$C' and mounts.alive('$C') else 1)"; t "describe и alive из приложения" test $? -eq 0
  t "состояние для веб-интерфейса" bash -c "vol() { \"$R/scripts/volumes.sh\" \"\$@\" --env \"$R/.env\"; }; vol list | jq -e '.items[0].state==\"attached\" and .items[0].free>0 and .propagates'"
  # отключение: на сервере том снят, в пустую точку писать нельзя
  umount -l "$MNT"
  vol reconcile
  t "после потери в контейнере том снят" not in_app "grep -q ' $C ' /proc/self/mountinfo"
  t "запись в пустую точку запрещена" not in_app "touch $C/lost.txt"
  t "на локальном диске ничего не появилось" test ! -e "$D/ext/$ID_/lost.txt"
  t "пустая точка принадлежит root и закрыта" test "$(stat -c '%U %a' "$D/ext/$ID_")" = "root 555"
  t "в каталог вместо ресурса ничего не подключается" bash -c "$R/scripts/volumes.sh reconcile --env $R/.env; ! findmnt --mountpoint $D/ext/$ID_"
  touch "$MNT/local-decoy" 2>/dev/null
  vol reconcile; t "локальный каталог на месте ресурса не принят за том" not findmnt --mountpoint "$D/ext/$ID_"
  rm -f "$MNT/local-decoy"
  mount "$MNT"; vol reconcile
  t "ресурс вернулся: том снова в контейнере" in_app "test -f $C/a.txt"
  # зависший сервер
  systemctl stop nfs-server
  docker exec app python3 -c "import sys,time; sys.path.insert(0,'/tmp'); import mounts; t=time.time(); a=mounts.alive('$C',3); print('alive',a,round(time.time()-t,1)); sys.exit(0 if not a and time.time()-t<5 else 1)"; t "недоступный сервер распознан без зависания приложения" test $? -eq 0
  systemctl start nfs-server; sleep 8
  t "сервер вернулся: запись снова работает" in_app "echo second > $C/b.txt"
  # запрос от приложения через помощника: только файл запроса от uid 10001
  "$R/scripts/updater.sh" install --env "$R/.env" --yes >/dev/null 2>&1; sleep 6
  vol detach "$ID_"
  rid=abcdef0123456789
  docker run --rm -u 10001:10001 -v "$D/updater:/u" python:3.12-alpine sh -c "printf 'id=$rid\naction=volume\nforce_build=0\npull=0\nop=attach\narg=$MNT\nby=admin\nat=%s\n' \$(date +%s) > /u/request.txt"
  wait_for "grep -q $rid $D/updater/volume-result.json" 30
  t "запрос из приложения выполнен помощником" jq -e '.ok==true' "$D/updater/volume-result.json"
  t "контейнер снова видит том" in_app "test -f $C/a.txt"
  rid2=abcdef0123456780
  docker run --rm -u 10001:10001 -v "$D/updater:/u" python:3.12-alpine sh -c "printf 'id=$rid2\naction=volume\nforce_build=0\npull=0\nop=attach\narg=/etc\nby=admin\nat=%s\n' \$(date +%s) > /u/request.txt"
  wait_for "grep -q $rid2 $D/updater/volume-result.json" 30
  t "запрос на /etc помощник отклонил" jq -e '.ok==false' "$D/updater/volume-result.json"
  ;;
reboot)
  ID_="$(ID)"; C="/data/ext/$ID_"
  wait_for "findmnt -t nfs4 $MNT" 90; t "после перезагрузки NFS смонтирован" findmnt -t nfs4 "$MNT"
  wait_for "findmnt --mountpoint $D/ext/$ID_" 90
  t "помощник подключил том сам" findmnt --mountpoint "$D/ext/$ID_"
  wait_for "docker exec app test -f $C/a.txt" 90
  t "контейнер видит том после перезагрузки" in_app "test -f $C/a.txt"
  t "запись после перезагрузки" in_app "echo third > $C/c.txt"
  ;;
*) echo "prep | main | reboot"; exit 2 ;;
esac
echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]
