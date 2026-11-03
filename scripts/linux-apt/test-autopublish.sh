#!/usr/bin/env bash
# test-autopublish.sh — проверка решений apt-autopublish.sh без сети и ключа: gh и curl подменены, вместо apt-publish.sh — заглушка.
# Случаи: нет выпусков; CI не закончил; версия на сайте; новая версия → публикация; в git сайта есть, сайт не отдаёт → перезапуск Pages;
# Pages ещё идёт → ждать; --force → публикация.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/apt-autopublish.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/root/scripts/linux-apt" "$T/bin" "$T/run"
cp "$SRC" "$T/root/scripts/linux-apt/"
printf '#!/bin/sh\necho PUBLISH\n' > "$T/root/scripts/linux-apt/apt-publish.sh"
# состояние «мира» — файлы в $T/w: tags, assets, site (версии на сайте), lock (pool.lock), pages (статус последнего запуска Pages)
cat > "$T/bin/gh" <<'EOF'
#!/bin/sh
case "$*" in
  "release list"*) cat "$W/tags" ;;
  "release view"*) cat "$W/assets" ;;
  "api repos/"*"/contents/pool.lock"*) cat "$W/lock" ;;
  "run list"*) cat "$W/pages" ;;
  "workflow run Pages"*) echo PAGES-RERUN ;;
  *) echo "gh: неожиданный вызов $*" >&2; exit 1 ;;
esac
EOF
printf '#!/bin/sh\nfor v in $(cat "$W/site"); do echo "Version: $v"; done\n' > "$T/bin/curl"
chmod +x "$T/bin/gh" "$T/bin/curl" "$T/root/scripts/linux-apt/"*.sh
W="$T/w"; mkdir -p "$W"; export W PATH="$T/bin:$PATH" XDG_RUNTIME_DIR="$T/run"
fails=0
world() { printf '%b' "$1" > "$W/tags"; printf '%b' "$2" > "$W/assets"; printf '%s' "$3" > "$W/site"; printf '%b' "$4" > "$W/lock"; printf '%s' "${5:-completed}" > "$W/pages"; }
expect() { local want="$1"; shift; local out; out="$("$T/root/scripts/linux-apt/apt-autopublish.sh" "$@" 2>&1)" || true
  if grep -q -- "$want" <<<"$out"; then echo "ok: $want"; else echo "ОШИБКА: ждали «$want», получили: $out"; fails=$((fails + 1)); fi; }
A2='peregovorka_2.0.0_amd64.deb\nperegovorka_2.0.0_arm64.deb\nSHA256SUMS-linux.txt\n'
L1='pool/main/p/peregovorka/peregovorka_1.0.0_amd64.deb x 1 https://github.com/o/r/releases/download/desktop-v1.0.0/peregovorka_1.0.0_amd64.deb\npool/main/p/peregovorka/peregovorka_1.0.0_arm64.deb x 1 https://github.com/o/r/releases/download/desktop-v1.0.0/peregovorka_1.0.0_arm64.deb\n'
L2="$L1$(printf '%b' "$L1" | sed 's/1\.0\.0/2.0.0/g')\n"
world 'v0.62.0\n' '' '' ''; expect "выпусков desktop-v нет"
world 'desktop-v1.0.0\ndesktop-v2.0.0\n' 'peregovorka_2.0.0_amd64.deb\n' '1.0.0' "$L1"; expect "ещё нет peregovorka_2.0.0_arm64.deb"
world 'desktop-v1.0.0\ndesktop-v2.0.0\n' "$A2" '1.0.0 2.0.0' "$L2"; expect "2.0.0 уже опубликована"
world 'desktop-v2.0.0\ndesktop-v1.0.0\ndesktop-v10.0.0-rc\n' "$A2" '1.0.0' "$L1"; expect "PUBLISH"
world 'desktop-v1.0.0\ndesktop-v2.0.0\n' "$A2" '1.0.0' "$L2"; expect "PAGES-RERUN"
world 'desktop-v1.0.0\ndesktop-v2.0.0\n' "$A2" '1.0.0' "$L2" in_progress; expect "идёт публикация GitHub Pages"
world 'desktop-v1.0.0\ndesktop-v2.0.0\n' "$A2" '1.0.0 2.0.0' "$L2"; expect "PUBLISH" --force
[ "$fails" = 0 ] && echo "test-autopublish: все случаи верны" || { echo "test-autopublish: ошибок $fails"; exit 1; }
