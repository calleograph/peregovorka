#!/usr/bin/env bash
# Обновление поверх установленного приложения на настоящем Android (эмулятор в CI): сборка с меньшим versionCode ставится, в каталоге приложения создаётся метка, затем
# ставится текущая сборка (`adb install -r`, тот же ключ подписи) — приложение обновляется, данные на месте; повторная установка той же сборки тоже проходит.
# Проверяет пакет, versionCode и сохранность данных; совместимость подписи РАЗНЫХ выпусков проверяет scripts/android_release_check.py.
set -euo pipefail
PKG=org.peregovorka.android
cd "$(dirname "$0")"
./gradlew --no-daemon :app:assembleDebug -PversionCodeOffset=-1 -q; cp app/build/outputs/apk/debug/app-debug.apk /tmp/pg-base.apk
./gradlew --no-daemon :app:assembleDebug -q;                         cp app/build/outputs/apk/debug/app-debug.apk /tmp/pg-new.apk
adb uninstall "$PKG" >/dev/null 2>&1 || true
adb install /tmp/pg-base.apk
base=$(adb shell dumpsys package "$PKG" | grep -m1 -o 'versionCode=[0-9]*' | cut -d= -f2)
adb shell "run-as $PKG sh -c 'mkdir -p files; echo kept-by-update > files/marker'"
adb install -r /tmp/pg-new.apk
new=$(adb shell dumpsys package "$PKG" | grep -m1 -o 'versionCode=[0-9]*' | cut -d= -f2)
echo "versionCode: $base → $new"
[ "$new" -gt "$base" ] || { echo "versionCode не вырос после обновления"; exit 1; }
adb shell "run-as $PKG cat files/marker" | grep -q kept-by-update || { echo "данные приложения потеряны при обновлении"; exit 1; }
adb install -r /tmp/pg-new.apk                       # повторная установка того же выпуска
adb shell "run-as $PKG cat files/marker" | grep -q kept-by-update || { echo "данные потеряны при повторной установке"; exit 1; }
echo "обновление поверх и повторная установка: данные сохранены"

# Сборки с постоянным ключом (если в CI есть секреты подписи): то же для release-APK. Release-сборка не отлаживаемая, поэтому сохранность данных видна по
# firstInstallTime (не меняется при обновлении поверх, сбрасывается при удалении и новой установке) и по тому, что установка прошла без удаления.
if [ -n "${PEREGOVORKA_KEYSTORE:-}" ]; then
  ./gradlew --no-daemon :app:assembleRelease -PversionCodeOffset=-1 -q; cp app/build/outputs/apk/release/app-release.apk /tmp/pg-rel-base.apk
  ./gradlew --no-daemon :app:assembleRelease -q;                         cp app/build/outputs/apk/release/app-release.apk /tmp/pg-rel-new.apk
  adb uninstall "$PKG" >/dev/null 2>&1 || true
  adb install /tmp/pg-rel-base.apk
  first=$(adb shell dumpsys package "$PKG" | grep -m1 -o 'firstInstallTime=[^ ]*.*' | tr -d '\r')
  sleep 2
  adb install -r /tmp/pg-rel-new.apk
  first2=$(adb shell dumpsys package "$PKG" | grep -m1 -o 'firstInstallTime=[^ ]*.*' | tr -d '\r')
  code=$(adb shell dumpsys package "$PKG" | grep -m1 -o 'versionCode=[0-9]*' | cut -d= -f2)
  echo "release: $first → $first2; versionCode $code"
  [ "$first" = "$first2" ] || { echo "release-обновление поверх переустановило приложение (данные потеряны бы)"; exit 1; }
  adb uninstall "$PKG" >/dev/null 2>&1 || true
  echo "обновление поверх сборкой с постоянным ключом: приложение обновлено без переустановки"
fi
