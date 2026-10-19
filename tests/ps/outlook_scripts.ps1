# Проверка сценариев Install/Uninstall/Test-OutlookAddin.ps1 на ПОДСТАВНОМ Exchange (функции New-App/Get-App/Remove-App с хранилищем в памяти).
# Настоящего Exchange нет: проверяется логика сценариев (идемпотентность, обновление, удаление, -WhatIf, журнал), а не поведение серверных командлетов.
param([Parameter(Mandatory)] [string]$Dir)
$ErrorActionPreference = 'Stop'
$global:Store = New-Object System.Collections.ArrayList
function global:New-App { param([switch]$OrganizationApp, [byte[]]$FileData, [string]$Url, $ProvidedTo, $DefaultStateForUser, $UserList)
    [xml]$x = [System.Text.Encoding]::UTF8.GetString($FileData).TrimStart([char]0xFEFF)
    [void]$global:Store.Add([pscustomobject]@{ AppId = $x.OfficeApp.Id; DisplayName = $x.OfficeApp.DisplayName.DefaultValue; Version = $x.OfficeApp.Version; Enabled = $true; ProvidedTo = $ProvidedTo }) }
function global:Get-App { param([switch]$OrganizationApp) $global:Store.ToArray() }
function global:Remove-App { param($Identity, [switch]$OrganizationApp, $Confirm) $keep = @($global:Store | Where-Object { $_.AppId -ne $Identity }); $global:Store.Clear(); $keep | ForEach-Object { [void]$global:Store.Add($_) } }
$fail = 0
function Check([string]$Name, [bool]$Cond) { if ($Cond) { Write-Host "PASS: $Name" } else { Write-Host "FAIL: $Name"; $script:fail++ } }
$manifest = Join-Path $Dir 'Peregovorka-Outlook.xml'
$log = Join-Path $Dir 'install.log'
& (Join-Path $Dir 'Install-OutlookAddin.ps1') -ManifestPath $manifest -LogPath $log -WhatIf | Out-Null
Check 'WhatIf ничего не устанавливает' ($global:Store.Count -eq 0)
& (Join-Path $Dir 'Install-OutlookAddin.ps1') -ManifestPath $manifest -LogPath $log | Out-Null
Check 'установка: одна запись' ($global:Store.Count -eq 1)
& (Join-Path $Dir 'Install-OutlookAddin.ps1') -ManifestPath $manifest -LogPath $log | Out-Null
Check 'повторный запуск не создаёт дубликат' ($global:Store.Count -eq 1)
Check 'журнал пишется' ((Get-Content -Raw $log) -match 'Уже установлена та же версия')
(Get-Content -Raw $manifest) -replace '<Version>[^<]+</Version>', '<Version>3.0.0</Version>' | Set-Content -NoNewline -Encoding utf8 (Join-Path $Dir 'new.xml')
& (Join-Path $Dir 'Install-OutlookAddin.ps1') -ManifestPath (Join-Path $Dir 'new.xml') -LogPath $log | Out-Null
Check 'новая версия без -Replace ничего не меняет' ($global:Store.Count -eq 1 -and $global:Store[0].Version -ne '3.0.0')
& (Join-Path $Dir 'Install-OutlookAddin.ps1') -ManifestPath (Join-Path $Dir 'new.xml') -LogPath $log -Replace | Out-Null
Check 'обновление -Replace: одна запись новой версии' ($global:Store.Count -eq 1 -and $global:Store[0].Version -eq '3.0.0')
& (Join-Path $Dir 'Test-OutlookAddin.ps1') -ManifestPath $manifest 2>&1 | Out-Null
& (Join-Path $Dir 'Uninstall-OutlookAddin.ps1') -ManifestPath $manifest -LogPath (Join-Path $Dir 'un.log') -WhatIf | Out-Null
Check 'удаление -WhatIf ничего не удаляет' ($global:Store.Count -eq 1)
& (Join-Path $Dir 'Uninstall-OutlookAddin.ps1') -ManifestPath $manifest -LogPath (Join-Path $Dir 'un.log') | Out-Null
Check 'удаление убирает запись' ($global:Store.Count -eq 0)
& (Join-Path $Dir 'Uninstall-OutlookAddin.ps1') -ManifestPath $manifest -LogPath (Join-Path $Dir 'un.log') | Out-Null
Check 'повторное удаление безопасно' ($global:Store.Count -eq 0)
& (Join-Path $Dir 'Install-OutlookAddin.ps1') -ManifestPath $manifest -LogPath $log -ProvidedTo SpecificUsers | Out-Null
Check 'SpecificUsers без -UserList отклонён' ($global:Store.Count -eq 0)
if ($fail -gt 0) { exit 1 }
