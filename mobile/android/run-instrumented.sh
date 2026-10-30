#!/usr/bin/env bash
# Инструментальные тесты на эмуляторе в два этапа.
#  1) Всё, кроме входа в конференцию с настоящим LiveKit: интерфейс, клавиатура, жесты приближения, Keystore — детерминированные проверки, одна попытка.
#  2) Вход в конференцию (JoinFlowTest, настоящий LiveKit и WebRTC): до трёх попыток. На эмуляторе без звука нативная библиотека WebRTC изредка падает (SIGABRT в потоке
#     AudioRecordJava) — падает процесс теста, а не приложение; такой сбой воспроизводится не всегда. Тест, который падает во всех трёх попытках, остаётся ошибкой.
set -uo pipefail
cd "$(dirname "$0")"
LK="-Pandroid.testInstrumentationRunnerArguments.livekit_url=${LIVEKIT_URL:-ws://10.0.2.2:7880}"
rc=0
./gradlew --no-daemon :app:connectedDebugAndroidTest "$LK" -Pandroid.testInstrumentationRunnerArguments.notClass=org.peregovorka.android.JoinFlowTest || rc=1
ok=0
for attempt in 1 2 3; do
  echo "=== вход в конференцию: попытка $attempt из 3 ==="
  if ./gradlew --no-daemon :app:connectedDebugAndroidTest "$LK" -Pandroid.testInstrumentationRunnerArguments.class=org.peregovorka.android.JoinFlowTest; then ok=1; break; fi
  mkdir -p app/build/join-attempts; cp -r app/build/outputs/androidTest-results "app/build/join-attempts/attempt-$attempt" 2>/dev/null || true
done
[ "$ok" = 1 ] || rc=1
exit "$rc"
