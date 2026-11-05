#!/bin/bash
# Пакеты на раннере CI: зависшее зеркало apt не должно давать красный CI (он блокирует выпуск), поэтому короткие попытки вместо одной длинной.
O=(-o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
for i in 1 2 3; do
  timeout -k 10 200 sudo apt-get "${O[@]}" update && timeout -k 10 200 sudo apt-get "${O[@]}" install -y --no-install-recommends "$@" && exit 0
  echo "apt: попытка $i не удалась"; sleep 15
done
exit 1
