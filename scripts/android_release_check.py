#!/usr/bin/env python3
"""Проверка APK перед публикацией: идентификатор пакета, версия, подпись и совместимость обновления с предыдущим выпуском.

  android_release_check.py НОВЫЙ.apk [--previous ПРЕДЫДУЩИЙ.apk] [--expect-key release|debug] [--json]

Почему это нужно: Android ставит обновление поверх установленного приложения, только если у нового APK тот же идентификатор пакета, `versionCode` БОЛЬШЕ и тот же сертификат
подписи. Сборки без секретов подписи подписываются отладочным ключом, который на каждом запуске CI создаётся заново, — поэтому «обновление» поверх прежней сборки
Android отклоняет («пакет обновления некорректен»/«конфликт пакетов»). Скрипт называет причину заранее и возвращает код:
  0 — обновление поверх предыдущего выпуска возможно (или предыдущего нет);
  1 — нарушены обязательные правила: пакет другой или versionCode не вырос;
  3 — пакет и версия в порядке, но сертификаты РАЗНЫЕ: обновление поверх невозможно, нужно удалить прежнюю версию (данные приложения при этом теряются).
Ключ подписи берётся из `apksigner verify --print-certs`, метаданные — из `aapt2 dump badging` (оба из Android SDK build-tools: переменная ANDROID_HOME или PATH)."""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys


def tool(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    found = sorted(glob.glob(os.path.join(home, "build-tools", "*", name)), key=lambda x: [int(n) if n.isdigit() else 0 for n in re.split(r"[.\-]", x.split(os.sep)[-2])])
    if not found:
        raise SystemExit(f"не найден {name}: задайте ANDROID_HOME (Android SDK build-tools)")
    return found[-1]


def parse_badging(text: str) -> dict:
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", text)
    if not m:
        raise ValueError("в выводе aapt2 нет строки package")
    return {"package": m.group(1), "version_code": int(m.group(2)), "version_name": m.group(3)}


def parse_certs(text: str) -> list[str]:
    """SHA-256 сертификатов подписи из `apksigner verify --print-certs` (все подписывающие, верхний регистр с двоеточиями)."""
    out = []
    for m in re.finditer(r"certificate SHA-256 digest:\s*([0-9a-fA-F:]+)", text):
        h = m.group(1).replace(":", "").lower()
        out.append(":".join(h[i:i + 2] for i in range(0, len(h), 2)).upper())
    return out


def inspect(apk: str) -> dict:
    meta = parse_badging(subprocess.run([tool("aapt2"), "dump", "badging", apk], capture_output=True, text=True, check=True).stdout)
    v = subprocess.run([tool("apksigner"), "verify", "--print-certs", apk], capture_output=True, text=True)
    if v.returncode != 0:
        raise SystemExit(f"подпись {apk} не проходит проверку: {(v.stderr or v.stdout).strip()[:300]}")
    meta["certs"] = parse_certs(v.stdout)
    if not meta["certs"]:
        raise SystemExit(f"в {apk} не найден сертификат подписи")
    return meta


def compare(new: dict, old: dict | None) -> tuple[int, list[str]]:
    """Код выхода и сообщения для людей. Чистая функция — проверяется тестами без Android SDK."""
    msgs: list[str] = []
    if old is None:
        return 0, ["Предыдущего выпуска для сравнения нет: проверка обновления поверх пропущена."]
    code = 0
    if new["package"] != old["package"]:
        msgs.append(f"ОШИБКА: идентификатор пакета изменился ({old['package']} → {new['package']}): Android считает это другим приложением, обновления поверх не будет.")
        code = 1
    if new["version_code"] <= old["version_code"]:
        msgs.append(f"ОШИБКА: versionCode не вырос ({old['version_code']} → {new['version_code']}): Android не ставит версию «не новее» поверх установленной.")
        code = 1
    if code == 0 and set(new["certs"]) != set(old["certs"]):
        msgs.append("ВНИМАНИЕ: сертификат подписи отличается от предыдущего выпуска — Android не установит эту версию поверх прежней («пакет обновления некорректен»). "
                    "Пользователю нужно удалить прежнюю версию (сохранённые серверы и вход придётся добавить заново). Так бывает, пока выпуски подписываются временным ключом сборки "
                    "(каждая сборка — свой ключ) либо при первом переходе на постоянный ключ. "
                    f"Было: {old['certs'][0]}; стало: {new['certs'][0]}.")
        code = 3
    if code == 0:
        msgs.append(f"Обновление {old['version_name']} ({old['version_code']}) → {new['version_name']} ({new['version_code']}) возможно: пакет и подпись те же, versionCode вырос.")
    return code, msgs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apk"); ap.add_argument("--previous"); ap.add_argument("--expect-key", choices=("release", "debug"), help="какой подписью должен быть подписан новый APK")
    ap.add_argument("--expect-cert", help="SHA-256 сертификата постоянного ключа (AA:BB:…): иначе — ошибка")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    new = inspect(a.apk)
    old = inspect(a.previous) if a.previous else None
    code, msgs = compare(new, old)
    if a.expect_key == "release" and any("Android Debug" in c for c in new["certs"]):
        msgs.append("ОШИБКА: ожидалась подпись постоянным ключом, а APK подписан отладочным."); code = max(code, 1)
    if a.expect_cert and a.expect_cert.strip().upper() not in new["certs"]:
        msgs.append(f"ОШИБКА: APK подписан не постоянным ключом организации (ожидался {a.expect_cert.strip().upper()})."); code = max(code, 1)
    if a.json:
        print(json.dumps({"new": new, "previous": old, "code": code, "messages": msgs}, ensure_ascii=False))
    else:
        print(f"{new['package']} {new['version_name']} ({new['version_code']}); подпись SHA-256: {', '.join(new['certs'])}")
        print("\n".join(msgs))
    return code


if __name__ == "__main__":
    sys.exit(main())
