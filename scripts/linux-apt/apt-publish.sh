#!/usr/bin/env bash
# apt-publish.sh — доверенная публикация репозитория APT Peregovorka (только на машине владельца: нужен закрытый ключ из .owner-private).
#   scripts/linux-apt/apt-publish.sh [--keep N] [--dry-run] [--skip-apt-check]
# 1) Пакеты — из выпусков GitHub desktop-vX.Y.Z основного репозитория (их собрал и проверил CI, workflow Linux): по N последних версий каждой архитектуры
#    (по умолчанию 4; старые остаются в выпусках). Каждый сверяется с SHA256SUMS-linux.txt своего выпуска и с полями пакета.
# 2) Индексы — make-repo.sh, подпись — sign-repo.sh (ключ владельца), проверка — verify-repo.py (подписи, хэши, полнота).
# 3) Испытание настоящим APT: чистая Ubuntu в контейнере ставит пакет через install.sh из собранного сайта (подпись проверяет сам apt).
# 4) Отправка в репозиторий сайта (calleograph/peregovorka-apt): индексы, pool.lock (путь, SHA-256, размер, адрес пакета), страница, install.sh, ключ.
#    GitHub Pages ещё раз проверяет всё и выкладывает сайт; затем сверяется опубликованный InRelease.
# --dry-run — всё, кроме отправки (сайт остаётся в рабочем каталоге). Ошибка на любом шаге — публикация не выполняется.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_REPO="calleograph/peregovorka"; APT_REPO="calleograph/peregovorka-apt"; APT_URL="https://calleograph.github.io/peregovorka-apt"
KEEP=4; DRY=0; APTCHECK=1
while [ $# -gt 0 ]; do
  case "$1" in --keep) KEEP="${2:?}"; shift 2 ;; --dry-run) DRY=1; shift ;; --skip-apt-check) APTCHECK=0; shift ;; *) echo "неизвестный аргумент: $1" >&2; exit 2 ;; esac
done
KEYRING="$ROOT/desktop/linux/keyring/peregovorka-archive-keyring.gpg"
FPR="$(gpg --batch --show-keys --with-colons "$KEYRING" 2>/dev/null | awk -F: '/^fpr/{print $10; exit}')"
[ "$FPR" = "$(tail -1 "$ROOT/.owner-private/linux-apt-signing/FINGERPRINT.txt")" ] || { echo "ключ в .owner-private не совпадает с открытым ключом проекта ($FPR)" >&2; exit 1; }
W="$(mktemp -d "${TMPDIR:-$HOME}/peregovorka-apt.XXXXXX")"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/debs"
echo "=== 1. пакеты из выпусков GitHub ($SRC_REPO)"
gh release list -R "$SRC_REPO" --limit 200 --json tagName --jq '.[].tagName' | grep -E '^desktop-v[0-9]+\.[0-9]+\.[0-9]+$' > "$W/tags" || true
: > "$W/assets"
while read -r tag; do
  gh release view "$tag" -R "$SRC_REPO" --json assets --jq '.assets[] | "\(.name) \(.url)"' | grep -E '^peregovorka_[0-9.]+_(amd64|arm64)\.deb ' | sed "s/^/$tag /" >> "$W/assets" || true
done < "$W/tags"
[ -s "$W/assets" ] || { echo "в выпусках нет пакетов .deb" >&2; exit 1; }
for a in amd64 arm64; do grep "_${a}\.deb " "$W/assets" | awk '{split($2,p,"_"); print p[2], $0}' | sort -V -r | head -n "$KEEP" | cut -d' ' -f2-; done > "$W/chosen"
while read -r tag name url; do
  gh release download "$tag" -R "$SRC_REPO" -p "$name" -p SHA256SUMS-linux.txt -D "$W/dl-$tag" --clobber
  (cd "$W/dl-$tag" && grep " $name\$" SHA256SUMS-linux.txt | sha256sum -c --quiet) || { echo "$name не совпал с SHA256SUMS-linux.txt выпуска $tag" >&2; exit 1; }
  cp "$W/dl-$tag/$name" "$W/debs/"
  echo "  $tag $name"
done < "$W/chosen"
NEWEST="$(for d in "$W"/debs/*.deb; do dpkg-deb -f "$d" Version; done | sort -V | tail -1)"

echo "=== 2. индексы, подпись, проверка"
SITE="$W/site"
"$ROOT/scripts/linux-apt/make-repo.sh" "$W/debs" "$SITE"
"$ROOT/scripts/linux-apt/sign-repo.sh" "$SITE"
cp "$KEYRING" "$ROOT/desktop/linux/keyring/peregovorka-archive-keyring.asc" "$ROOT/desktop/linux/install.sh" "$SITE/"
printf '# Отпечаток ключа подписи репозитория APT Peregovorka\n%s\n' "$FPR" > "$SITE/FINGERPRINT"
DEB_NAME="peregovorka_${NEWEST}_amd64.deb"
sed -e "s#@VERSION@#$NEWEST#g" -e "s#@DEB_NAME@#$DEB_NAME#g" -e "s#@DEB_URL@#https://github.com/$SRC_REPO/releases/download/desktop-v$NEWEST/$DEB_NAME#g" \
    -e "s#@FINGERPRINT_SPACED@#$(echo "$FPR" | sed 's/.\{4\}/& /g;s/ $//')#g" -e "s#@DATE@#$(LC_ALL=C date -u '+%Y-%m-%d %H:%M UTC')#g" \
    "$ROOT/desktop/linux/apt-site/index.html" > "$SITE/index.html"
python3 "$ROOT/scripts/linux-apt/verify-repo.py" "$SITE" "$KEYRING" --expect-fingerprint "$FPR" --require-version "$NEWEST"

if [ "$APTCHECK" = 1 ]; then
  echo "=== 3. испытание настоящим APT (чистая Ubuntu 24.04, install.sh)"
  PORT=18799; while ss -ltn | awk '{print $4}' | grep -q ":$PORT\$"; do PORT=$((PORT + 1)); done
  python3 "$ROOT/desktop/linux/test/faultserver.py" "$SITE" "$PORT" >/dev/null 2>&1 & SRV=$!
  sleep 1
  got="$(docker run --rm --network host -v "$SITE/install.sh:/install.sh:ro" ubuntu:24.04 bash -c \
    "PEREGOVORKA_APT_URL=http://127.0.0.1:$PORT PEREGOVORKA_APT_INSECURE_TEST=1 sh /install.sh >/tmp/i.log 2>&1 || tail -5 /tmp/i.log; dpkg-query -W -f '\${Version}' peregovorka 2>/dev/null")" || true
  kill "$SRV" 2>/dev/null || true
  [ "${got##*$'\n'}" = "$NEWEST" ] || { echo "APT не установил $NEWEST из собранного репозитория: $got" >&2; exit 1; }
  echo "  установлено $NEWEST"
fi

echo "=== 4. репозиторий сайта ($APT_REPO)"
git clone -q "https://github.com/$APT_REPO.git" "$W/git"
G="$W/git"
rm -rf "$G/dists"; cp -r "$SITE/dists" "$G/"
cp "$SITE/index.html" "$SITE/install.sh" "$SITE/peregovorka-archive-keyring.gpg" "$SITE/peregovorka-archive-keyring.asc" "$SITE/FINGERPRINT" "$G/"
cp "$ROOT/scripts/linux-apt/verify-repo.py" "$G/"; cp "$ROOT/desktop/linux/apt-site/README.md" "$G/"
mkdir -p "$G/.github/workflows"; cp "$ROOT/desktop/linux/apt-site/.github/workflows/pages.yml" "$G/.github/workflows/"
: > "$G/pool.lock"
for d in "$SITE"/pool/main/p/peregovorka/*.deb; do
  n="$(basename "$d")"; v="$(dpkg-deb -f "$d" Version)"
  printf '%s %s %s %s\n' "pool/main/p/peregovorka/$n" "$(sha256sum "$d" | cut -d' ' -f1)" "$(stat -c %s "$d")" "https://github.com/$SRC_REPO/releases/download/desktop-v$v/$n" >> "$G/pool.lock"
done
if grep -rlaE -- "-----BEGIN (PGP |RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY" "$G" --exclude-dir=.git | grep -q .; then echo "в репозитории сайта закрытый ключ — остановлено" >&2; exit 1; fi
git -C "$G" add -A
if git -C "$G" diff --cached --quiet; then echo "изменений нет"; exit 0; fi
git -C "$G" -c user.name="$(git -C "$ROOT" config user.name)" -c user.email="$(git -C "$ROOT" config user.email)" commit -q -m "Репозиторий APT: Peregovorka $NEWEST ($(wc -l < "$G/pool.lock") пакет(ов))"
if [ "$DRY" = 1 ]; then echo "--dry-run: не отправлено; проверенный сайт: $SITE (удалится по выходу)"; git -C "$G" show --stat HEAD | tail -12; exit 0; fi
git -C "$G" push -q origin HEAD
echo "отправлено; жду публикацию GitHub Pages"
sleep 15
run="$(gh run list -R "$APT_REPO" --workflow Pages --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$run" -R "$APT_REPO" --exit-status --interval 15 >/dev/null || { echo "публикация Pages не прошла — сайт остался прежним: gh run view $run -R $APT_REPO --log-failed" >&2; exit 1; }
for i in $(seq 20); do
  curl -fsS "$APT_URL/dists/stable/InRelease" -o "$W/live-InRelease" 2>/dev/null && cmp -s "$W/live-InRelease" "$SITE/dists/stable/InRelease" && break; sleep 15
done
cmp -s "$W/live-InRelease" "$SITE/dists/stable/InRelease" || { echo "опубликованный InRelease не совпал (кэш CDN?) — проверьте позже" >&2; exit 1; }
echo "опубликовано: $APT_URL (Peregovorka $NEWEST)"
