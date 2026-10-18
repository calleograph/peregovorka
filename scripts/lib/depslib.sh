#!/usr/bin/env bash
# depslib.sh — сверка РАБОТАЮЩИХ компонентов с манифестом релиза (backend/app/components.json) после обновления; логика — scripts/deps.py postcheck.
# deps_postcheck → печатает отчёт, пишет $DATA_ROOT/updater/components-report.json (его показывает «Версии компонентов») и возвращает:
#   0 — комплект соответствует релизу, 1 — есть расхождения (предупреждение), 2 — критические расхождения (LiveKit, SDK, PostgreSQL, Redis: работает не то, что проверено в релизе),
#   3 — проверку выполнить не удалось (нет python3 или docker): не считается успехом, но обновление не останавливает.
deps_postcheck() {
  local py rc out
  py="$(command -v python3 || true)"
  [ -n "$py" ] || { warn "python3 не найден: сверка компонентов с релизом пропущена (поставьте python3 и выполните: python3 scripts/deps.py postcheck)"; return 3; }
  compose_args
  out="$DATA_ROOT/updater/components-report.json"
  DEPS_COMPOSE_JSON="$("$py" -c 'import json,sys;print(json.dumps(sys.argv[1:]))' docker "${COMPOSE_ARGS[@]}")" "$py" "$REPO_ROOT/scripts/deps.py" postcheck --env "$ENV_FILE" --out "$out"
  rc=$?
  chmod 644 "$out" 2>/dev/null || true
  return "$rc"
}
