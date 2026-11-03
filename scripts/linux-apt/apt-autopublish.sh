#!/usr/bin/env bash
# apt-autopublish.sh — автоматическая публикация репозитория APT после выпуска (запускается таймером systemd на машине владельца, где лежит ключ).
#   scripts/linux-apt/apt-autopublish.sh            — проверить и при необходимости опубликовать
#   scripts/linux-apt/apt-autopublish.sh --install  — поставить таймер пользователя (каждые 10 минут; ключ не покидает машину)
#   scripts/linux-apt/apt-autopublish.sh --remove   — убрать таймер
# Публикует, только когда у последнего выпуска desktop-vX.Y.Z уже есть пакеты обеих архитектур и SHA256SUMS-linux.txt (их добавляет CI после
# всех испытаний), а опубликованный pool.lock этого пакета ещё не содержит. Сама публикация — apt-publish.sh со всеми его проверками.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_REPO="calleograph/peregovorka"; APT_REPO="calleograph/peregovorka-apt"; UNIT=peregovorka-apt-autopublish
case "${1:-}" in
  --install)
    d="$HOME/.config/systemd/user"; mkdir -p "$d"
    printf '[Unit]\nDescription=Peregovorka: публикация репозитория APT после выпуска\n\n[Service]\nType=oneshot\nExecStart="%s"\nNice=10\n' "$ROOT/scripts/linux-apt/apt-autopublish.sh" > "$d/$UNIT.service"
    printf '[Unit]\nDescription=Peregovorka: проверка новых выпусков для репозитория APT\n\n[Timer]\nOnBootSec=5min\nOnUnitActiveSec=10min\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' > "$d/$UNIT.timer"
    systemctl --user daemon-reload && systemctl --user enable --now "$UNIT.timer"
    echo "таймер включён: systemctl --user status $UNIT.timer; журнал: journalctl --user -u $UNIT"; exit 0 ;;
  --remove)
    systemctl --user disable --now "$UNIT.timer" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/$UNIT".{service,timer}; systemctl --user daemon-reload; echo "таймер убран"; exit 0 ;;
  "") ;;
  *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
esac
exec 9>"${XDG_RUNTIME_DIR:-$HOME}/$UNIT.lock"; flock -n 9 || { echo "публикация уже идёт"; exit 0; }

tag="$(gh release list -R "$SRC_REPO" --limit 50 --json tagName,isDraft --jq '.[] | select(.isDraft | not) | .tagName' | grep -E '^desktop-v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
[ -n "$tag" ] || { echo "выпусков desktop-v нет"; exit 0; }
v="${tag#desktop-v}"
assets="$(gh release view "$tag" -R "$SRC_REPO" --json assets --jq '.assets[].name')"
for f in "peregovorka_${v}_amd64.deb" "peregovorka_${v}_arm64.deb" SHA256SUMS-linux.txt; do
  grep -qx "$f" <<<"$assets" || { echo "$tag: ещё нет $f (CI не закончил) — позже"; exit 0; }
done
lock="$(gh api "repos/$APT_REPO/contents/pool.lock" -H "Accept: application/vnd.github.raw" 2>/dev/null || true)"
grep -q "/desktop-v$v/peregovorka_${v}_amd64.deb\$" <<<"$lock" && { echo "$v уже опубликована"; exit 0; }
echo "публикую $v"
exec "$ROOT/scripts/linux-apt/apt-publish.sh"
