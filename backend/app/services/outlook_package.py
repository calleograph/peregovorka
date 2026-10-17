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
  Установка надстройки Peregovorka для Outlook в локальном Exchange.
.DESCRIPTION
  Запускайте в Exchange Management Shell под учётной записью с ролью «Org Management» (управление приложениями организации).
  Сценарий читает манифест рядом с собой и добавляет его как приложение организации. Повторный запуск без -Replace ничего не меняет.
  -WhatIf показывает действие без выполнения.
.EXAMPLE
  .\Install-OutlookAddin.ps1 -WhatIf
.EXAMPLE
  .\Install-OutlookAddin.ps1 -ProvidedTo SpecificUsers -UserList 'ivanov@example.org','petrova@example.org'
.EXAMPLE
  .\Install-OutlookAddin.ps1 -Replace      # обновление: старая запись удаляется, добавляется новая с тем же идентификатором
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ManifestPath = (Join-Path $PSScriptRoot 'Peregovorka-Outlook.xml'),
    [ValidateSet('Everyone', 'SpecificUsers')] [string]$ProvidedTo = 'Everyone',
    [string[]]$UserList = @(),
    [ValidateSet('Enabled', 'Disabled', 'AlwaysEnabled')] [string]$DefaultState = 'Enabled',
    [switch]$Replace
)
$ErrorActionPreference = 'Stop'

if (-not (Get-Command New-App -ErrorAction SilentlyContinue)) {
    throw 'Команда New-App не найдена. Запустите сценарий в Exchange Management Shell (а не в обычном PowerShell).'
}
if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Файл манифеста не найден: $ManifestPath" }
if ($ProvidedTo -eq 'SpecificUsers' -and $UserList.Count -eq 0) { throw 'Для SpecificUsers укажите -UserList.' }

[xml]$manifest = Get-Content -Raw -LiteralPath $ManifestPath
$appId = [string]$manifest.OfficeApp.Id
$name = [string]$manifest.OfficeApp.DisplayName.DefaultValue
if (-not $appId) { throw 'В манифесте нет идентификатора надстройки (OfficeApp/Id).' }
Write-Host "Надстройка: $name, идентификатор $appId, версия $($manifest.OfficeApp.Version)"

$existing = @(Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq $appId })
if ($existing.Count -gt 0) {
    if (-not $Replace) {
        Write-Host 'Надстройка уже установлена. Для обновления запустите сценарий с ключом -Replace.'
        return
    }
    if ($PSCmdlet.ShouldProcess($appId, 'Удалить прежнюю запись приложения организации')) {
        Remove-App -Identity $appId -OrganizationApp -Confirm:$false
    }
}

$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $ManifestPath).Path)
$args2 = @{ OrganizationApp = $true; FileData = $bytes; ProvidedTo = $ProvidedTo; DefaultStateForUser = $DefaultState }
if ($ProvidedTo -eq 'SpecificUsers') { $args2['UserList'] = $UserList }
if ($PSCmdlet.ShouldProcess($appId, 'Установить надстройку для организации')) {
    New-App @args2 | Out-Null
    Write-Host 'Готово. Клиентам Outlook нужно до нескольких часов, чтобы получить надстройку; перезапустите Outlook.'
    Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq $appId } | Format-List DisplayName, AppId, Enabled, ProvidedTo
}
"""

TEST_PS1 = r"""<#
.SYNOPSIS
  Проверка условий для надстройки Peregovorka для Outlook: манифест, доступность адресов, сертификат HTTPS, состояние в Exchange.
.DESCRIPTION
  Запускайте на компьютере, откуда доступен сервер Peregovorka (лучше — с рабочей станции пользователя Outlook). Часть «Exchange» выполняется,
  только если сценарий запущен в Exchange Management Shell. Ничего не изменяет.
#>
[CmdletBinding()]
param([string]$ManifestPath = (Join-Path $PSScriptRoot 'Peregovorka-Outlook.xml'))
$ErrorActionPreference = 'Continue'
$results = New-Object System.Collections.ArrayList
function Add-Result([string]$Name, [bool]$Ok, [string]$Detail) { [void]$results.Add([pscustomobject]@{ Проверка = $Name; Результат = $(if ($Ok) { 'OK' } else { 'ОШИБКА' }); Подробности = $Detail }) }

try {
    [xml]$m = Get-Content -Raw -LiteralPath $ManifestPath
    Add-Result 'Манифест читается' $true "Id $($m.OfficeApp.Id), версия $($m.OfficeApp.Version)"
} catch { Add-Result 'Манифест читается' $false $_.Exception.Message; $results | Format-Table -AutoSize -Wrap; exit 1 }

$urls = New-Object System.Collections.ArrayList
$m.SelectNodes('//*[@DefaultValue]') | ForEach-Object { if ($_.DefaultValue -match '^https?://') { [void]$urls.Add($_.DefaultValue) } }
$urls = @($urls | Sort-Object -Unique)
foreach ($u in $urls) {
    try {
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -Method Get -TimeoutSec 15
        Add-Result "Адрес открывается: $u" ($r.StatusCode -eq 200) "HTTP $($r.StatusCode)"
    } catch { Add-Result "Адрес открывается: $u" $false $_.Exception.Message }
}

$hostName = ([uri]$urls[0]).Host
try {
    $client = New-Object System.Net.Sockets.TcpClient($hostName, 443)
    $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false)
    $ssl.AuthenticateAsClient($hostName)
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
    $days = [int]($cert.NotAfter - (Get-Date)).TotalDays
    Add-Result "Сертификат HTTPS ($hostName)" ($days -gt 14) "действует до $($cert.NotAfter.ToString('yyyy-MM-dd')) (осталось дней: $days), выдан: $($cert.Issuer)"
    $client.Close()
} catch { Add-Result "Сертификат HTTPS ($hostName)" $false "$($_.Exception.Message) — Outlook не загрузит надстройку с недоверенным сертификатом" }

if (Get-Command Get-App -ErrorAction SilentlyContinue) {
    $app = @(Get-App -OrganizationApp | Where-Object { ([string]$_.AppId) -eq [string]$m.OfficeApp.Id })
    Add-Result 'Надстройка установлена в Exchange' ($app.Count -gt 0) $(if ($app.Count -gt 0) { "включена: $($app[0].Enabled)" } else { 'не найдена: запустите Install-OutlookAddin.ps1' })
} else {
    Add-Result 'Состояние в Exchange' $true 'пропущено: сценарий запущен не в Exchange Management Shell'
}

$results | Format-Table -AutoSize -Wrap
if (@($results | Where-Object { $_.Результат -ne 'OK' }).Count -gt 0) { exit 1 }
"""


def _readme(base: str, version: str, cfg: OutlookSettings) -> str:
    host = urlsplit(base).hostname or base
    sync = ("включена (в манифесте подписка на отправку приглашения, нужен Mailbox 1.12)" if cfg.sync_on_send else "выключена")
    return f"""НАДСТРОЙКА PEREGOVORKA ДЛЯ OUTLOOK — установочный пакет для сервера {host}
Версия Peregovorka: {version}. Сформировано сервером автоматически; в пакете нет паролей и ключей.

СОСТАВ
  Peregovorka-Outlook.xml     манифест с адресами вашего сервера ({base}) и постоянным идентификатором надстройки
  Install-OutlookAddin.ps1    установка в локальный Exchange (Exchange Management Shell)
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
  Exchange: Get-App -OrganizationApp | ? DisplayName -eq 'Peregovorka' | Remove-App -OrganizationApp
  Microsoft 365: Integrated apps → Peregovorka → Remove.
  Конференции и ссылки в уже отправленных приглашениях продолжают работать (комнаты хранятся в Peregovorka).

ОГРАНИЧЕНИЯ
  Сценарии PowerShell не запускались на настоящем Exchange автором пакета: сначала прогоните их на тестовой базе. Надстройка не проверялась в настоящем
  Outlook/Exchange. Подробности и матрица совместимости — docs/OUTLOOK_ADDIN.md в репозитории.
"""


def build_package(base: str, version: str, cfg: OutlookSettings) -> bytes:
    """ZIP-пакет для установки администратором Exchange / Microsoft 365."""
    files = {"Peregovorka-Outlook.xml": ol.manifest_xml(base, version, sync_on_send=cfg.sync_on_send), "README.txt": _readme(base, version, cfg),
             "Install-OutlookAddin.ps1": BOM + INSTALL_PS1, "Test-OutlookAddin.ps1": BOM + TEST_PS1}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))     # фиксированная дата: пакет воспроизводим, повторное скачивание даёт тот же архив
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    return buf.getvalue()
