#!/usr/bin/env bash
# sign-repo.sh — подпись метаданных репозитория APT закрытым ключом владельца (только на доверенной машине, не в CI):
#   scripts/linux-apt/sign-repo.sh <каталог-сайта> [<каталог-ключа>]     (по умолчанию .owner-private/linux-apt-signing)
# Создаёт dists/stable/InRelease (подписанный Release) и dists/stable/Release.gpg (отдельная подпись). Ключ берётся из копии каталога GnuPG
# во временном каталоге с правами 700, которая удаляется по выходу: рабочий каталог владельца не меняется. Несколько ключей (переходный период
# при смене ключа) — переменная SIGNING_KEYS со списком отпечатков: подпись каждым, у клиента достаточно одного известного ему.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SITE="${1:?каталог сайта}"; KEYDIR="${2:-$ROOT/.owner-private/linux-apt-signing}"
R="$SITE/dists/stable/Release"
[ -f "$R" ] || { echo "нет $R (сначала make-repo.sh)" >&2; exit 1; }
[ -d "$KEYDIR/gnupg" ] && [ -f "$KEYDIR/passphrase.txt" ] || { echo "нет ключа в $KEYDIR (gnupg/, passphrase.txt)" >&2; exit 1; }
[ "$(stat -c %a "$KEYDIR")" = 700 ] || { echo "права $KEYDIR должны быть 700 (сейчас $(stat -c %a "$KEYDIR"))" >&2; exit 1; }
KEYS="${SIGNING_KEYS:-$(tail -1 "$KEYDIR/FINGERPRINT.txt")}"

TMPG="$(mktemp -d)"; trap 'rm -rf "$TMPG"' EXIT
cp -r "$KEYDIR/gnupg/." "$TMPG/"; chmod 700 "$TMPG"
G=(gpg --homedir "$TMPG" --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$KEYDIR/passphrase.txt" --digest-algo SHA512)
U=(); for k in $KEYS; do [[ "$k" =~ ^[0-9A-F]{40}$ ]] || { echo "не отпечаток ключа: $k" >&2; exit 1; }; U+=(--local-user "$k"); done
rm -f "$SITE/dists/stable/InRelease" "$SITE/dists/stable/Release.gpg"
"${G[@]}" "${U[@]}" --clearsign --output "$SITE/dists/stable/InRelease" "$R"
"${G[@]}" "${U[@]}" --armor --detach-sign --output "$SITE/dists/stable/Release.gpg" "$R"
chmod 644 "$SITE/dists/stable/InRelease" "$SITE/dists/stable/Release.gpg"
echo "подписано ключами: $KEYS"
