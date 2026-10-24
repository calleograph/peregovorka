#!/bin/sh
# Первичная настройка подписывающего узла Peregovorka (Linux/macOS, OpenSSL 3): собственный сертификат подписи кода и ключ обновлений.
# Для узла на Windows — Peregovorka-Signer.ps1 (сертификат в хранилище Windows, закрытый ключ не экспортируется).
#
#   desktop/signing/signing-node-init.sh [каталог] [издатель]      каталог по умолчанию: ~/.local/share/peregovorka-signing
#
# Создаёт (права 0700/0600, повторный запуск ничего не перезаписывает):
#   publisher.pfx + publisher.pfx.pass   сертификат Code Signing с закрытым ключом (RSA 3072, SHA-256, EKU 1.3.6.1.5.5.7.3.3), пароль PFX — случайный
#   publisher.cer                        ТОЛЬКО открытый сертификат (DER) — его можно раздавать для доверия
#   update-key.pem + update-key.pub      пара Ed25519 для подписи манифестов обновлений (отдельно от подписи кода!)
#   meta.json                            издатель, срок действия, серийный номер, отпечатки SHA-1/SHA-256, открытый ключ обновлений
# Самоподписанный сертификат НЕ доверен на компьютерах сотрудников, пока администратор сам не распространит publisher.cer (docs/DESKTOP_SIGNING.md).
set -eu
umask 077
DIR="${1:-$HOME/.local/share/peregovorka-signing}"
SUBJECT="${2:-Peregovorka Test Publisher}"
mkdir -p "$DIR"
chmod 700 "$DIR"
cd "$DIR"

if [ ! -f publisher.pfx ]; then
  cat > cs.cnf <<EOF
[req]
distinguished_name = dn
prompt = no
x509_extensions = ext
[dn]
CN = $SUBJECT
O = $SUBJECT
[ext]
keyUsage = critical, digitalSignature
extendedKeyUsage = codeSigning
subjectKeyIdentifier = hash
EOF
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out publisher.key 2>/dev/null
  openssl req -new -x509 -key publisher.key -sha256 -days 1095 -config cs.cnf -out publisher.pem
  openssl rand -base64 33 | tr -d '\n' > publisher.pfx.pass
  openssl pkcs12 -export -inkey publisher.key -in publisher.pem -name "$SUBJECT" -out publisher.pfx -passout file:publisher.pfx.pass \
    -keypbe AES-256-CBC -certpbe AES-256-CBC -macalg sha256
  openssl x509 -in publisher.pem -outform der -out publisher.cer
  rm -f publisher.key cs.cnf                      # закрытый ключ остаётся только внутри PFX (зашифрован паролем)
  echo "сертификат подписи создан: $DIR/publisher.pfx"
else
  echo "сертификат подписи уже есть — не перезаписан"
fi

if [ ! -f update-key.pem ]; then
  openssl genpkey -algorithm ed25519 -out update-key.pem
  openssl pkey -in update-key.pem -pubout -outform der | tail -c 32 | base64 > update-key.pub
  echo "ключ обновлений создан: $DIR/update-key.pem"
else
  echo "ключ обновлений уже есть — не перезаписан"
fi

chmod 600 publisher.pfx publisher.pfx.pass update-key.pem
chmod 644 publisher.cer publisher.pem update-key.pub
fp() { openssl x509 -in publisher.pem -noout -fingerprint "-$1" | sed 's/.*=//; s/://g'; }
cat > meta.json <<EOF
{
 "type": "self-signed",
 "subject": "$(openssl x509 -in publisher.pem -noout -subject -nameopt RFC2253 | sed 's/^subject=//')",
 "issuer": "$(openssl x509 -in publisher.pem -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//')",
 "serial": "$(openssl x509 -in publisher.pem -noout -serial | sed 's/serial=//')",
 "not_before": "$(openssl x509 -in publisher.pem -noout -startdate | sed 's/notBefore=//')",
 "not_after": "$(openssl x509 -in publisher.pem -noout -enddate | sed 's/notAfter=//')",
 "thumbprint_sha1": "$(fp sha1)",
 "sha256": "$(fp sha256)",
 "eku": "1.3.6.1.5.5.7.3.3",
 "key": "RSA 3072",
 "update_pubkey": "$(cat update-key.pub)"
}
EOF
chmod 644 meta.json
cat meta.json
