#!/bin/bash
# Пакеты на раннере CI: зависшее зеркало apt не должно давать красный CI (он блокирует выпуск), поэтому короткие попытки вместо одной длинной.
# APT_ARCHIVES — каталог скачанных .deb (его хранит actions/cache): при медленном зеркале следующий прогон не качает их заново.
O=(-o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
if [ -n "${APT_ARCHIVES:-}" ]; then mkdir -p "$APT_ARCHIVES/partial"; O+=(-o "Dir::Cache::Archives=$APT_ARCHIVES"); fi
ok=1
for i in $(seq 1 "${APT_TRIES:-3}"); do
  timeout -k 10 200 sudo apt-get "${O[@]}" update && timeout -k 10 300 sudo apt-get "${O[@]}" install -y --no-install-recommends "$@" && { ok=0; break; }
  echo "apt: попытка $i не удалась"; sleep 15
done
# кэш сохраняет пользователь раннера: файлы apt (root) делаем читаемыми, недокачанное не сохраняем
if [ -n "${APT_ARCHIVES:-}" ]; then sudo rm -rf "$APT_ARCHIVES/partial" "$APT_ARCHIVES/lock"; sudo chown -R "$(id -u):$(id -g)" "$APT_ARCHIVES"; fi
exit $ok
