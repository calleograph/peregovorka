#!/bin/bash
# Подмена пути тома на настоящем mount: контейнер с CAP_SYS_ADMIN (не privileged, systemd нет). Приложение (uid 10001) непрерывно меняет каталог на ссылку на корень, root монтирует через safefs.py.
#   docker run --rm --cap-add SYS_ADMIN --security-opt apparmor=unconfined --security-opt seccomp=unconfined -v "$PWD:/r:ro" <образ с python3 и util-linux> bash /r/tests/live/volumes/swap_race.sh
# Проверка живая: при `CHECK_BROKEN=1` монтирует старым способом (проверка, потом mount --bind) и показывает, что подмена проходит.
set -u
V=/mnt/vol; mkdir -p "$V/real" /ext/mp; chown -R 10001:10001 "$V"; touch "$V/real/.vol-marker"
setpriv --reuid=10001 --regid=10001 --clear-groups sh -c "cd $V; while :; do mv real real.x 2>/dev/null; ln -sfn / real; rm -f real; mv real.x real 2>/dev/null; done" &
swapper=$!
N="${ROUNDS:-3000}"; ok=0; refused=0; leaked=0
for i in $(seq "$N"); do
  if [ "${CHECK_BROKEN:-0}" = 1 ]; then
    if [ "$(readlink -f "$V/real")" = "$V/real" ]; then mount --bind "$V/real" /ext/mp 2>/dev/null; rc=$?; else rc=3; fi
  else
    python3 -I /r/scripts/safefs.py bind "$V/real" /ext/mp 2>/dev/null; rc=$?
  fi
  if [ "$rc" = 0 ]; then
    ok=$((ok+1))
    [ -e /ext/mp/etc/passwd ] && leaked=$((leaked+1))
    umount /ext/mp
  else refused=$((refused+1)); fi
done
kill "$swapper" 2>/dev/null
echo "попыток=$N смонтировано=$ok отказов=$refused корень_хоста_в_точке=$leaked"
[ "$leaked" = 0 ] && [ "$ok" -gt 0 ]
