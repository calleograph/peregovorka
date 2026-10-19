"""Установочный пакет надстройки Outlook для конкретного сервера Peregovorka: манифест, инструкции и сценарии PowerShell.

В пакет входят только параметры подключения (адреса этой установки, стабильный идентификатор надстройки, версия). Пароли, ключи API и секреты сверки в него не попадают.
Сценарии PowerShell сохраняются с BOM (иначе Windows PowerShell 5.1 неверно читает русские строки) и **не проверялись на настоящем Exchange**: синтаксис разобран
парсером PowerShell, логика требует пробного запуска администратором на тестовой почтовой базе.
"""
from __future__ import annotations

import io
import zipfile
from urllib.parse import urlsplit

from . import outlook as ol
from .settings import OutlookSettings

BOM = "﻿"

INSTALL_PS1 = r"""<#
.SYNOPSIS
  Установка (и обновление) надстройки Peregovorka для Outlook в Exchange Server 2016/2019/Subscription Edition и Exchange Online.
.DESCRIPTION
  Запускайте в Exchange Management Shell (Exchange Online — после Connect-ExchangeOnline) под учётной записью с ролью «Org Marketplace Apps» или «Org Management».
  Сценарий идемпотентен: если надстройка с этим идентификатором уже установлена в той же версии, ничего не меняется и дубликат не создаётся.
  Все действия пишутся в журнал (-LogPath). Адреса и идентификаторы берутся из манифеста; в сценарии ничего не зашито.
  Манифест — файл рядом со сценарием либо прямая ссылка (-ManifestUrl): она должна открываться по HTTPS БЕЗ перенаправлений (Exchange 2019 за перенаправлениями не идёт).
.EXAMPLE
  .\Install-OutlookAddin.ps1 -WhatIf
.EXAMPLE
  .\Install-OutlookAddin.ps1 -ProvidedTo SpecificUsers -UserList 'ivanov@example.org','petrova@example.org'
.EXAMPLE
  .\Install-OutlookAddin.ps1 -ManifestUrl 'https://meet.example.org/api/v1/outlook/manifest.xml'
.EXAMPLE
  .\Install-OutlookAddin.ps1 -Replace      # обновление: прежняя запись заменяется новой с тем же идентификатором
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ManifestPath = (Join-Path $PSScriptRoot 'Peregovorka-Outlook.xml'),
    [string]$ManifestUrl,
    [ValidateSet('Everyone', 'SpecificUsers')] [string]$ProvidedTo = 'Everyone',
    [string[]]$UserList = @(),
    [ValidateSet('Enabled', 'Disabled', 'AlwaysEnabled')] [string]$DefaultState = 'Enabled',
    [switch]$Replace,
    [string]$LogPath = (Join-Path (Get-Location) ('Peregovorka-Outlook-install-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date)))
)
$ErrorActionPreference = 'Stop'
function Write-Log([string]$Text, [string]$Level = 'INFO') {
    $line = '{0:yyyy-MM-dd HH:mm:ss} [{1}] {2}' -f (Get-Date), $Level, $Text
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    if ($Level -eq 'ERROR') { Write-Host $Text -ForegroundColor Red } else { Write-Host $Text }
}
try {
    Write-Log "Журнал: $LogPath"
    foreach ($cmd in 'New-App', 'Get-App', 'Remove-App') {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "Команда $cmd не найдена. Запустите сценарий в Exchange Management Shell (для Exchange Online — после Connect-ExchangeOnline)." }
    }
    try { $null = Get-App -OrganizationApp -ErrorAction Stop }
    catch { throw "Нет прав на управление приложениями организации: нужна роль «Org Marketplace Apps» или «Org Management». Подробности: $($_.Exception.Message)" }

    if ($ManifestUrl) {
        if ($ManifestUrl -notmatch '^https://') { throw 'ManifestUrl должен начинаться с https://' }
        $ManifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ('Peregovorka-Outlook-{0}.xml' -f [guid]::NewGuid())
        try { Invoke-WebRequest -Uri $ManifestUrl -UseBasicParsing -MaximumRedirection 0 -OutFile $ManifestPath -TimeoutSec 30 }
        catch { throw "Манифест не загружен прямой ссылкой без перенаправлений: $($_.Exception.Message)" }
        Write-Log "Манифест загружен: $ManifestUrl"
    }
    if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Файл манифеста не найден: $ManifestPath" }
    [xml]$manifest = Get-Content -Raw -LiteralPath $ManifestPath
    $appId = [string]$manifest.OfficeApp.Id
    $name = [string]$manifest.OfficeApp.DisplayName.DefaultValue
    $version = [string]$manifest.OfficeApp.Version
    if (-not $appId) { throw 'В манифесте нет идентификатора надстройки (OfficeApp/Id).' }
    if ($ProvidedTo -eq 'SpecificUsers' -and $UserList.Count -eq 0) { throw 'Для SpecificUsers укажите -UserList.' }
    Write-Log "Надстройка: $name, идентификатор $appId, версия манифеста $version"

    $existing = @(Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq $appId })
    if ($existing.Count -gt 0) {
        $installedVersion = [string]$existing[0].Version
        if (-not $Replace) {
            if ($installedVersion -eq $version) { Write-Log "Уже установлена та же версия ($installedVersion): изменений нет." }
            else { Write-Log "Установлена версия $installedVersion, в манифесте $version. Для обновления запустите сценарий с ключом -Replace." }
            return
        }
        if ($PSCmdlet.ShouldProcess($appId, 'Удалить прежнюю запись приложения организации (обновление)')) {
            Remove-App -Identity $appId -OrganizationApp -Confirm:$false
            Write-Log "Прежняя запись удалена (версия $installedVersion)."
        }
    }

    $params = @{ OrganizationApp = $true; ProvidedTo = $ProvidedTo; DefaultStateForUser = $DefaultState }
    if ($ProvidedTo -eq 'SpecificUsers') { $params['UserList'] = $UserList }
    if ($ManifestUrl) { $params['Url'] = $ManifestUrl } else { $params['FileData'] = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $ManifestPath).Path) }
    if ($PSCmdlet.ShouldProcess($appId, "Установить надстройку (доступ: $ProvidedTo)")) {
        New-App @params | Out-Null
        Write-Log 'Надстройка добавлена.'
        $after = @(Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq $appId })
        if ($after.Count -ne 1) { throw "После установки найдено записей: $($after.Count) (ожидалась 1). Проверьте Get-App -OrganizationApp." }
        Write-Log ("Проверка: {0}, включена: {1}, доступ: {2}" -f $after[0].DisplayName, $after[0].Enabled, $after[0].ProvidedTo)
        Write-Log 'Готово. Клиентам Outlook нужно до нескольких часов, чтобы получить надстройку; перезапустите Outlook.'
    }
}
catch {
    Write-Log $_.Exception.Message 'ERROR'
    exit 1
}
"""

UNINSTALL_PS1 = r"""<#
.SYNOPSIS
  Удаление надстройки Peregovorka для Outlook из организации Exchange (Exchange Management Shell или Exchange Online).
.DESCRIPTION
  Идентификатор берётся из манифеста рядом со сценарием (или -AppId). Если надстройки нет, сценарий ничего не делает. Конференции и ссылки в отправленных приглашениях продолжают работать.
.EXAMPLE
  .\Uninstall-OutlookAddin.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ManifestPath = (Join-Path $PSScriptRoot 'Peregovorka-Outlook.xml'),
    [string]$AppId,
    [string]$LogPath = (Join-Path (Get-Location) ('Peregovorka-Outlook-uninstall-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date)))
)
$ErrorActionPreference = 'Stop'
function Write-Log([string]$Text, [string]$Level = 'INFO') {
    Add-Content -LiteralPath $LogPath -Value ('{0:yyyy-MM-dd HH:mm:ss} [{1}] {2}' -f (Get-Date), $Level, $Text) -Encoding UTF8
    if ($Level -eq 'ERROR') { Write-Host $Text -ForegroundColor Red } else { Write-Host $Text }
}
try {
    foreach ($cmd in 'Get-App', 'Remove-App') { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "Команда $cmd не найдена: запустите сценарий в Exchange Management Shell." } }
    if (-not $AppId) {
        if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Укажите -AppId или положите манифест рядом: $ManifestPath" }
        [xml]$m = Get-Content -Raw -LiteralPath $ManifestPath
        $AppId = [string]$m.OfficeApp.Id
    }
    $found = @(Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq $AppId })
    if ($found.Count -eq 0) { Write-Log "Надстройка $AppId не установлена: ничего не делаю."; return }
    if ($PSCmdlet.ShouldProcess($AppId, 'Удалить надстройку организации')) {
        Remove-App -Identity $AppId -OrganizationApp -Confirm:$false
        $left = @(Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq $AppId })
        if ($left.Count -gt 0) { throw 'Надстройка осталась в списке после удаления: проверьте права и повторите.' }
        Write-Log "Надстройка $AppId удалена."
    }
}
catch { Write-Log $_.Exception.Message 'ERROR'; exit 1 }
"""

TEST_PS1 = r"""<#
.SYNOPSIS
  Проверка условий для надстройки Peregovorka для Outlook: манифест, прямая загрузка без перенаправлений, адреса, цепочка сертификатов HTTPS, состояние в Exchange.
.DESCRIPTION
  Запускайте на компьютере, откуда доступен сервер Peregovorka (лучше — с рабочей станции пользователя Outlook): так проверяется ровно то, что увидит клиент.
  Часть «Exchange» выполняется, только если сценарий запущен в Exchange Management Shell. Ничего не изменяет.
#>
[CmdletBinding()]
param([string]$ManifestPath = (Join-Path $PSScriptRoot 'Peregovorka-Outlook.xml'), [string]$ManifestUrl)
$ErrorActionPreference = 'Continue'
$results = New-Object System.Collections.ArrayList
function Add-Result([string]$Name, [bool]$Ok, [string]$Detail) { [void]$results.Add([pscustomobject]@{ Проверка = $Name; Результат = $(if ($Ok) { 'OK' } else { 'ОШИБКА' }); Подробности = $Detail }) }

try {
    if ($ManifestUrl) {
        $resp = Invoke-WebRequest -Uri $ManifestUrl -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 20
        Add-Result 'Манифест по прямой ссылке (без перенаправлений)' ($resp.StatusCode -eq 200) "HTTP $($resp.StatusCode), Content-Type: $($resp.Headers['Content-Type'])"
        [xml]$m = $resp.Content
    } else { [xml]$m = Get-Content -Raw -LiteralPath $ManifestPath }
    Add-Result 'Манифест читается как XML' $true "Id $($m.OfficeApp.Id), версия $($m.OfficeApp.Version)"
} catch { Add-Result 'Манифест' $false $_.Exception.Message; $results | Format-Table -AutoSize -Wrap; exit 1 }

$urls = New-Object System.Collections.ArrayList
$m.SelectNodes('//*[@DefaultValue]') | ForEach-Object { if ($_.DefaultValue -match '^https?://') { [void]$urls.Add($_.DefaultValue) } }
$urls = @($urls | Sort-Object -Unique)
Add-Result 'Все адреса в манифесте — HTTPS' (@($urls | Where-Object { $_ -notmatch '^https://' }).Count -eq 0) "адресов: $($urls.Count)"
foreach ($u in $urls) {
    try {
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -Method Get -TimeoutSec 15 -MaximumRedirection 0
        Add-Result "Адрес открывается: $u" ($r.StatusCode -eq 200) "HTTP $($r.StatusCode)"
    } catch { Add-Result "Адрес открывается: $u" $false $_.Exception.Message }
}

$hostName = ([uri]$urls[0]).Host
try {
    $client = New-Object System.Net.Sockets.TcpClient($hostName, 443)
    $script:policyErrors = 'None'; $script:chainInfo = @()
    $callback = [System.Net.Security.RemoteCertificateValidationCallback]{ param($s, $c, $chain, $errors) $script:policyErrors = [string]$errors; $script:chainInfo = @($chain.ChainElements | ForEach-Object { $_.Certificate.Subject }); $true }
    $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false, $callback)
    $ssl.AuthenticateAsClient($hostName)
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
    $days = [int]($cert.NotAfter - (Get-Date)).TotalDays
    $trusted = ($script:policyErrors -eq 'None')
    Add-Result "Сертификат HTTPS ($hostName): доверие этого компьютера" $trusted "ошибки политики: $($script:policyErrors); цепочка: $($script:chainInfo -join ' → ')"
    Add-Result "Сертификат HTTPS ($hostName): срок действия" ($days -gt 14) "до $($cert.NotAfter.ToString('yyyy-MM-dd')) (осталось дней: $days), выдан: $($cert.Issuer)"
    if (-not $trusted) { Add-Result 'Что делать' $false 'RemoteCertificateChainErrors — нет доверия цепочке: корневой/промежуточный сертификат не установлен на этом компьютере либо прокси отдаёт неполную цепочку (исправляется на прокси: fullchain). RemoteCertificateNameMismatch — имя сайта не в сертификате.' }
    $client.Close()
} catch { Add-Result "Сертификат HTTPS ($hostName)" $false "$($_.Exception.Message) — Outlook не загрузит надстройку без доверия сертификату" }

if (Get-Command Get-App -ErrorAction SilentlyContinue) {
    $app = @(Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq [string]$m.OfficeApp.Id })
    Add-Result 'Надстройка установлена в Exchange' ($app.Count -gt 0) $(if ($app.Count -gt 0) { "включена: $($app[0].Enabled), версия: $($app[0].Version), записей: $($app.Count)" } else { 'не найдена: запустите Install-OutlookAddin.ps1' })
    if ($app.Count -gt 1) { Add-Result 'Дубликаты' $false 'найдено несколько записей с одним идентификатором: удалите лишние (Uninstall-OutlookAddin.ps1, затем Install-OutlookAddin.ps1)' }
} else {
    Add-Result 'Состояние в Exchange' $true 'пропущено: сценарий запущен не в Exchange Management Shell'
}

$results | Format-Table -AutoSize -Wrap
if (@($results | Where-Object { $_.Результат -ne 'OK' }).Count -gt 0) { exit 1 }
"""


def _readme(base: str, version: str, cfg: OutlookSettings) -> str:
    host = urlsplit(base).hostname or base
    sync = ("включена (в манифесте подписка на отправку приглашения, нужен Mailbox 1.12)" if cfg.effective_sync_on_send else "выключена" + (" (запрошена, но не применяется: локальный Exchange поддерживает Mailbox API не выше 1.5; используйте серверную сверку EWS)" if cfg.sync_on_send else ""))
    return f"""НАДСТРОЙКА PEREGOVORKA ДЛЯ OUTLOOK — установочный пакет для сервера {host}
Версия Peregovorka: {version}. Сформировано сервером автоматически; в пакете нет паролей и ключей.

СОСТАВ
  Peregovorka-Outlook.xml     манифест с адресами вашего сервера ({base}) и постоянным идентификатором надстройки
  Install-OutlookAddin.ps1    установка и обновление в Exchange (Exchange Management Shell; идемпотентно, с журналом)
  Uninstall-OutlookAddin.ps1  удаление надстройки из организации
  Test-OutlookAddin.ps1       проверка условий: манифест, адреса, сертификат HTTPS, состояние в Exchange
  README.txt                  эта инструкция (обновление и удаление — в конце)

ЧТО ДОЛЖНО БЫТЬ ГОТОВО
  1. Сервер Peregovorka доступен клиентам Outlook по {base} с действующим сертификатом, которому доверяют рабочие станции (самоподписанный не подойдёт).
  2. В Peregovorka включена интеграция («Администрирование → Интеграции → Microsoft Outlook»).
  3. Надстройку устанавливает администратор: только такая надстройка заменяет в окне встречи переключатель Teams/Skype кнопкой Peregovorka.

УСТАНОВКА
  А. Локальный Exchange 2019 (основной сценарий)
     1) Распакуйте архив на сервере Exchange или рабочей станции с Exchange Management Shell.
     2) Проверка:   .\\Test-OutlookAddin.ps1
     3) Установка:  .\\Install-OutlookAddin.ps1 -WhatIf     (посмотреть) затем   .\\Install-OutlookAddin.ps1
        Только избранным:  .\\Install-OutlookAddin.ps1 -ProvidedTo SpecificUsers -UserList 'user1@example.org'
     Либо в Exchange admin center: Organization → Add-ins → «+» → Add from file → выберите Peregovorka-Outlook.xml.
     Клиенты получают надстройку не сразу (до нескольких часов); перезапустите Outlook.
  Б. Microsoft 365 / Exchange Online
     Центр администрирования Microsoft 365 → Settings → Integrated apps → Upload custom apps → тип «Office Add-in» → выберите Peregovorka-Outlook.xml →
     назначьте пользователей или группы. Распространение занимает до 24 часов.
  В. Проверка у одного пользователя (не для промышленной эксплуатации)
     Outlook в браузере: Get Add-ins → My add-ins → Custom add-ins → Add from file. Надстройка, добавленная пользователем, показывает кнопку на ленте,
     но не заменяет переключатель Teams/Skype.

ПОЛЬЗОВАТЕЛЬ
  Календарь → «Новое собрание» → на ленте окна встречи группа «Peregovorka» → «Добавить конференцию». Первый раз откроется окно входа Peregovorka
  (пароль и двухфакторная защита, если включена). Ссылка вставляется в приглашение; нажмите обычную кнопку «Отправить».
  Серверная синхронизация изменений: {sync}.

ОБНОВЛЕНИЕ
  Идентификатор надстройки зависит только от адреса сервера ({base}) и не меняется при повторном скачивании. Логика надстройки обновляется вместе с сервером
  Peregovorka (клиентам ничего ставить не нужно). Переустанавливать манифест требуется, если изменился адрес сервера или настройка «синхронизация при отправке»:
  скачайте пакет заново и выполните  .\\Install-OutlookAddin.ps1 -Replace  (или замените файл в центре администрирования).
  Если надстройка включает отправку-синхронизацию (событие OnAppointmentSend), администратор Microsoft 365 должен повторно подтвердить обновлённый манифест.

УДАЛЕНИЕ
  Exchange: .\\Uninstall-OutlookAddin.ps1  (или Remove-App -Identity <Id> -OrganizationApp)
  Microsoft 365: Integrated apps → Peregovorka → Remove.
  Конференции и ссылки в уже отправленных приглашениях продолжают работать (комнаты хранятся в Peregovorka).

ОГРАНИЧЕНИЯ
  Сценарии не запускались на настоящем Exchange автором пакета (синтаксис разобран парсером PowerShell): сначала прогоните их на тестовой базе -WhatIf. Подробности и матрица совместимости — docs/OUTLOOK_ADDIN.md в репозитории.
"""


def build_package(base: str, version: str, cfg: OutlookSettings) -> bytes:
    """ZIP-пакет для установки администратором Exchange / Microsoft 365."""
    files = {"Peregovorka-Outlook.xml": ol.manifest_xml(base, version, sync_on_send=cfg.effective_sync_on_send), "README.txt": _readme(base, version, cfg),
             "Install-OutlookAddin.ps1": BOM + INSTALL_PS1, "Test-OutlookAddin.ps1": BOM + TEST_PS1,
             "Uninstall-OutlookAddin.ps1": BOM + UNINSTALL_PS1}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))     # фиксированная дата: пакет воспроизводим, повторное скачивание даёт тот же архив
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    return buf.getvalue()
