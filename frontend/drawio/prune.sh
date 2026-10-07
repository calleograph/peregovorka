#!/bin/sh
# Оставляет из исходников draw.io (jgraph/drawio, Apache-2.0) только то, что нужно встроенному редактору схем:
# статический редактор (index.html + js/mxgraph/shapes/stencils/styles/images/img), русский и английский словари интерфейса.
# Облачные интеграции (Dropbox, OneDrive, GitHub, GitLab, Teams, Confluence/Jira-коннекторы), шаблоны, математика (MathJax),
# service worker, серверные части (WEB-INF) не берутся: редактор работает офлайн внутри нашего web-контейнера и ничего не отправляет наружу.
# Использование: prune.sh <каталог-клона-drawio> <каталог-результата>
set -eu

SRC="${1:?укажите каталог клона drawio}/src/main/webapp"
OUT="${2:?укажите каталог результата}"
[ -f "$SRC/index.html" ] || { echo "prune.sh: $SRC/index.html не найден — это не клон jgraph/drawio" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT/js" "$OUT/resources"

for f in index.html open.html clear.html export3.html vsdxImporter.html export-fonts.css favicon.ico shortcuts.svg; do
    [ -f "$SRC/$f" ] && cp "$SRC/$f" "$OUT/$f"
done
for d in images img mxgraph plugins shapes stencils styles; do
    [ -d "$SRC/$d" ] && cp -R "$SRC/$d" "$OUT/$d"
done

# js: основные бандлы и библиотеки, которые подгружает редактор; интеграции и исходники grapheditor/diagramly (дублируют *.min.js) не нужны
for f in app.min.js extensions.min.js stencils.min.js shapes-14-6-5.min.js export.js export-init.js bootstrap.js open.js \
         vsdxImporter.js math-print.js clear.js PreConfig.js PostConfig.js main.js; do
    [ -f "$SRC/js/$f" ] && cp "$SRC/js/$f" "$OUT/js/$f"
done
for d in cryptojs deflate elk freehand jquery jszip libavoid-js mermaid plantuml rough sanitizer simplepeer spin; do
    [ -d "$SRC/js/$d" ] && cp -R "$SRC/js/$d" "$OUT/js/$d"
done

# интерфейс: английский (по умолчанию) и русский
cp "$SRC/resources/dia.txt" "$OUT/resources/dia.txt"
[ -f "$SRC/resources/dia_ru.txt" ] && cp "$SRC/resources/dia_ru.txt" "$OUT/resources/dia_ru.txt"

# Текст лицензии Apache-2.0 и уведомления сопровождают распространяемые файлы
cp "$1/LICENSE" "$OUT/LICENSE"
[ -f "$1/NOTICE" ] && cp "$1/NOTICE" "$OUT/NOTICE"
echo "draw.io: $(du -sk "$OUT" | cut -f1) КБ, файлов: $(find "$OUT" -type f | wc -l)"
