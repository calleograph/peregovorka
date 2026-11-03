#!/usr/bin/env bash
# apt-autopublish.sh — автоматическая публикация репозитория APT после выпуска (запускается таймером systemd на машине владельца, где лежит ключ).
#   scripts/linux-apt/apt-autopublish.sh            — проверить и при необходимости опубликовать
#   scripts/linux-apt/apt-autopublish.sh --install  — поставить таймер пользователя (каждые 10 минут; ключ не покидает машину)
#   scripts/linux-apt/apt-autopublish.sh --remove   — убрать таймер
#   scripts/linux-apt/apt-autopublish.sh --force    — опубликовать заново, даже если версия уже на сайте (ручная проверка, починка)
# Публикует, только когда у последнего выпуска desktop-vX.Y.Z уже есть пакеты обеих архитектур и SHA256SUMS-linux.txt (их добавляет CI после
# всех испытаний), а сайт (то, что видит apt пользователей) эту версию ещё не отдаёт. Сама публикация — apt-publish.sh со всеми его проверками.
# Версия уже в репозитории сайта, но сайт её не отдаёт (сбой GitHub Pages) — перезапускается публикация Pages. Проверка: test-autopublish.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_REPO="calleograph/peregovorka"; APT_REPO="calleograph/peregovorka-apt"; APT_URL="https://calleograph.github.io/peregovorka-apt"
UNIT=peregovorka-apt-autopublish; FORCE=0
case "${1:-}" in
  --install)
    d="$HOME/.config/systemd/user"; mkdir -p "$d"
    printf '[Unit]\nDescription=Peregovorka: публикация репозитория APT после выпуска\n\n[Service]\nType=oneshot\nExecStart="%s"\nNice=10\nTimeoutStartSec=45min\n' "$ROOT/scripts/linux-apt/apt-autopublish.sh" > "$d/$UNIT.service"
    printf '[Unit]\nDescription=Peregovorka: проверка новых выпусков для репозитория APT\n\n[Timer]\nOnBootSec=5min\nOnUnitActiveSec=10min\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n' > "$d/$UNIT.timer"
    systemctl --user daemon-reload && systemctl --user enable --now "$UNIT.timer"
    echo "таймер включён: systemctl --user status $UNIT.timer; журнал: journalctl --user -u $UNIT"; exit 0 ;;
  --remove)
    systemctl --user disable --now "$UNIT.timer" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/$UNIT".{service,timer}; systemctl --user daemon-reload; echo "таймер убран"; exit 0 ;;
  --force) FORCE=1 ;;
  "") ;;
  *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
esac
exec 9>"${XDG_RUNTIME_DIR:-$HOME}/$UNIT.lock"; flock -n 9 || { echo "публикация уже идёт"; exit 0; }

tag="$(gh release list -R "$SRC_REPO" --limit 200 --json tagName,isDraft --jq '.[] | select(.isDraft | not) | .tagName' | { grep -E '^desktop-v[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -V | tail -1)"
[ -n "$tag" ] || { echo "выпусков desktop-v нет"; exit 0; }
v="${tag#desktop-v}"
assets="$(gh release view "$tag" -R "$SRC_REPO" --json assets --jq '.assets[].name')"
for f in "peregovorka_${v}_amd64.deb" "peregovorka_${v}_arm64.deb" SHA256SUMS-linux.txt; do
  grep -qx "$f" <<<"$assets" || { echo "$tag: ещё нет $f (CI не закончил) — позже"; exit 0; }
done
if [ "$FORCE" = 0 ]; then
  live() { curl -fsS --max-time 60 "$APT_URL/dists/stable/main/binary-$1/Packages?t=$(date +%s)" 2>/dev/null | grep -qx "Version: $v"; }
  if live amd64 && live arm64; then echo "$v уже опубликована"; exit 0; fi
  lock="$(gh api "repos/$APT_REPO/contents/pool.lock" -H "Accept: application/vnd.github.raw" 2>/dev/null || true)"
  if grep -q "/desktop-v$v/peregovorka_${v}_arm64.deb\$" <<<"$lock" && grep -q "/desktop-v$v/peregovorka_${v}_amd64.deb\$" <<<"$lock"; then
    st="$(gh run list -R "$APT_REPO" --workflow Pages --limit 1 --json status --jq '.[0].status' 2>/dev/null || true)"
    if [ -n "$st" ] && [ "$st" != completed ]; then echo "$v: идёт публикация GitHub Pages — позже"; exit 0; fi
    echo "$v уже в репозитории сайта, но сайт её не отдаёт — перезапускаю публикацию GitHub Pages"
    gh workflow run Pages -R "$APT_REPO" --ref main; exit 0
  fi
fi
echo "публикую $v"
exec "$ROOT/scripts/linux-apt/apt-publish.sh"
