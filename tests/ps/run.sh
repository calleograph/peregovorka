#!/usr/bin/env bash
# Сценарии PowerShell: разбор парсером и прогон на подставном Exchange (нужен Docker; образ PowerShell ~300 МБ). Использование: tests/ps/run.sh [python]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; PY="${1:-python3}"
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT; chmod 777 "$D"
(cd "$ROOT/backend" && "$PY" - "$D" <<'PYEOF'
import sys
from app.services import outlook as ol, outlook_package as pk
d = sys.argv[1]
for name, text in (("Install-OutlookAddin.ps1", pk.INSTALL_PS1), ("Test-OutlookAddin.ps1", pk.TEST_PS1), ("Uninstall-OutlookAddin.ps1", pk.UNINSTALL_PS1)):
    open(f"{d}/{name}", "w", encoding="utf-8-sig", newline="").write(text.replace("\n", "\r\n"))
open(f"{d}/Peregovorka-Outlook.xml", "w", encoding="utf-8", newline="").write(ol.manifest_xml("https://meet.example.org", "0.0.0"))
PYEOF
)
cp "$ROOT/tests/ps/outlook_scripts.ps1" "$D/"
docker run --rm -v "$D:/t" mcr.microsoft.com/powershell:lts-debian-12 pwsh -NoProfile -Command '
$bad = 0
foreach ($f in Get-ChildItem /t/*-OutlookAddin.ps1) { $e = $null; $t = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$t, [ref]$e); if ($e.Count) { $bad++; $e | ForEach-Object { "$($f.Name): $($_.Message)" } } }
if ($bad) { exit 1 }; Write-Host "PASS: разбор парсером PowerShell: ошибок нет"
& /t/outlook_scripts.ps1 -Dir /t'
