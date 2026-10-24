<#
.SYNOPSIS
  Подписывающий агент Peregovorka: подпись установщиков клиента Windows на доверенном узле организации.

.DESCRIPTION
  Закрытый ключ подписи остаётся в хранилище Windows этого компьютера (Cert:\LocalMachine\My), в том числе неэкспортируемый; ни сервер Peregovorka,
  ни браузер администратора его не получают. Агент сам опрашивает сервер и выполняет только задания фиксированных видов:
    create_cert   — создать собственный (самоподписанный) сертификат подписи кода;
    register_cert — найти сертификат организации по отпечатку и сообщить его сведения и открытую часть;
    check_cert    — проверить назначение, срок, цепочку и пробную подпись;
    build         — корпоративный выпуск: скачать проверенную версию клиента из ЛОКАЛЬНО заданного источника, сверить SHA-256 и издателя,
                    вписать настройки организации в MSI, подписать EXE/MSI, проверить подписи, отдать пакеты серверу.
  Произвольных команд и адресов агент от сервера не принимает. Подробно — docs/DESKTOP_SIGNING.md.

.EXAMPLE
  .\Peregovorka-Signer.ps1 Enroll -Server https://meet.example.org -Code PGE-XXXX -Name "Узел подписи"
  .\Peregovorka-Signer.ps1 Install          # задача планировщика: агент работает от SYSTEM при запуске компьютера
  .\Peregovorka-Signer.ps1 Run              # работа в текущем окне (для проверки)
  .\Peregovorka-Signer.ps1 ListCerts        # сертификаты с назначением «Подпись кода» и закрытым ключом
  .\Peregovorka-Signer.ps1 ImportPfx -Path C:\keys\codesign.pfx     # пароль спрашивается здесь, ключ становится неэкспортируемым
  .\Peregovorka-Signer.ps1 NewCert -Subject "ООО Пример"
  .\Peregovorka-Signer.ps1 CheckCert -Thumbprint <SHA-1>
  .\Peregovorka-Signer.ps1 SignFiles -Thumbprint <SHA-1> -Files a.exe,b.msi   # локальная подпись (CI, разработчик) без сервера
  .\Peregovorka-Signer.ps1 Build -JobFile job.json -OutDir out [-SourceDir dir]  # корпоративная сборка без сервера (те же проверки)
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0, Mandatory = $true)]
  [ValidateSet("Enroll", "Run", "Install", "Uninstall", "ListCerts", "ImportPfx", "NewCert", "CheckCert", "SignFiles", "Build", "Status")]
  [string]$Command,
  [string]$Server,
  [string]$Code,
  [string]$Name = $env:COMPUTERNAME,
  [string]$ReleaseBase = "https://github.com/calleograph/peregovorka/releases/download",
  [string]$PublisherThumbprint = "",
  [string]$TimestampUrl = "",
  [string]$Subject,
  [int]$Years = 3,
  [string]$Thumbprint,
  [string]$Path,
  [string[]]$Files,
  [string]$JobFile,
  [string]$OutDir,
  [string]$SourceDir,
  [string]$ConfigDir = $(if ($env:PEREGOVORKA_SIGNER_DIR) { $env:PEREGOVORKA_SIGNER_DIR } elseif ($env:ProgramData) { Join-Path $env:ProgramData "Peregovorka\Signer" } else { Join-Path $HOME ".peregovorka-signer" }),
  [switch]$AllowHttpLoopback,
  [switch]$Once,
  [switch]$NoTrustOnNode
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$AgentVersion = "1.1.0"
$CodeSigningOid = "1.3.6.1.5.5.7.3.3"
$IsWin = [Environment]::OSVersion.Platform -eq "Win32NT"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol

function Fail([string]$m) { throw [System.Exception]::new($m) }

# ------------------------------------------------------------------------------------------------ конфигурация и токен агента
function Protect-Dir([string]$d) {
  New-Item -ItemType Directory -Force $d | Out-Null
  if ($IsWin) {
    # только администраторы и SYSTEM: токен агента не должен читать обычный пользователь узла
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in "S-1-5-32-544", "S-1-5-18") {
      $id = New-Object Security.Principal.SecurityIdentifier $sid
      $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule $id, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow"))
    }
    Set-Acl -Path $d -AclObject $acl
  }
}

function Save-Token([string]$tok) {
  $f = Join-Path $ConfigDir "token.bin"
  $bytes = [Text.Encoding]::UTF8.GetBytes($tok)
  if ($IsWin) {
    Add-Type -AssemblyName System.Security
    $bytes = [Security.Cryptography.ProtectedData]::Protect($bytes, [Text.Encoding]::UTF8.GetBytes("peregovorka-signer"), [Security.Cryptography.DataProtectionScope]::LocalMachine)
  } else {
    Write-Warning "не Windows: токен хранится без DPAPI (только для испытаний)"
  }
  [IO.File]::WriteAllBytes($f, $bytes)
  if (-not $IsWin) { chmod 600 $f }
}

function Read-Token {
  $bytes = [IO.File]::ReadAllBytes((Join-Path $ConfigDir "token.bin"))
  if ($IsWin) {
    Add-Type -AssemblyName System.Security
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect($bytes, [Text.Encoding]::UTF8.GetBytes("peregovorka-signer"), [Security.Cryptography.DataProtectionScope]::LocalMachine)
  }
  [Text.Encoding]::UTF8.GetString($bytes)
}

function Read-Config {
  $f = Join-Path $ConfigDir "agent.json"
  if (-not (Test-Path $f)) { Fail "агент не подключён: сначала Enroll" }
  Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Assert-ServerUrl([string]$u) {
  if ($u -match '^https://[^/\s]+/?$') { return $u.TrimEnd("/") }
  if ($AllowHttpLoopback -and $u -match '^http://(127\.0\.0\.1|localhost)(:\d+)?/?$') { return $u.TrimEnd("/") }
  Fail "адрес сервера — https://имя[:порт] (незащищённое соединение не допускается)"
}

function Api([string]$method, [string]$path, $body = $null) {
  $cfg = Read-Config
  $h = @{ Authorization = "Bearer $(Read-Token)" }
  $req = @{ Method = $method; Uri = "$($cfg.server)/api/v1$path"; Headers = $h; TimeoutSec = 60; UseBasicParsing = $true }
  if ($null -ne $body) { $req.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 8 -Compress)); $req.ContentType = "application/json; charset=utf-8" }
  Invoke-RestMethod @req
}

function Upload([string]$jobId, [string]$file) {
  $cfg = Read-Config
  $name = [Uri]::EscapeDataString((Split-Path $file -Leaf))
  Invoke-RestMethod -Method Put -Uri "$($cfg.server)/api/v1/signing/agent/jobs/$jobId/files/$name" -Headers @{ Authorization = "Bearer $(Read-Token)" } -InFile $file `
    -ContentType "application/octet-stream" -TimeoutSec 1800 -UseBasicParsing | Out-Null
}

function Host-Info {
  @{ os = [Environment]::OSVersion.VersionString; powershell = $PSVersionTable.PSVersion.ToString(); signtool = [bool](Find-SignTool); release_base = (Read-Config).release_base;
     publisher_pin = [bool](Read-Config).publisher_thumbprint }
}

# ------------------------------------------------------------------------------------------------ сертификаты
function Find-SignTool {
  if (-not $IsWin) { return $null }
  $k = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
  if ($k) { return $k.FullName }
  $c = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  return $null
}

function Find-Cert([string]$tp) {
  $tp = ($tp -replace '[^0-9A-Fa-f]', '').ToUpper()
  foreach ($store in "Cert:\LocalMachine\My", "Cert:\CurrentUser\My") {
    $c = Get-ChildItem $store -ErrorAction SilentlyContinue | Where-Object Thumbprint -eq $tp | Select-Object -First 1
    if ($c) { return $c }
  }
  Fail "сертификат $tp не найден в хранилищах LocalMachine\My и CurrentUser\My этого узла"
}

function Test-Cert($cert, [bool]$trySign = $true) {
  $eku = @($cert.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId })
  $chain = New-Object Security.Cryptography.X509Certificates.X509Chain
  $chain.ChainPolicy.RevocationMode = "Online"
  $chain.ChainPolicy.RevocationFlag = "ExcludeRoot"
  $chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromSeconds(15)
  [void]$chain.ChainPolicy.ApplicationPolicy.Add((New-Object Security.Cryptography.Oid $CodeSigningOid))
  $built = $chain.Build($cert)
  $status = @($chain.ChainStatus | ForEach-Object { "$($_.Status): $($_.StatusInformation.Trim())" })
  # нет связи с точкой отзыва — не повод считать цепочку недоверенной, но в отчёте видно
  $hard = @($chain.ChainStatus | Where-Object { $_.Status -notin "RevocationStatusUnknown", "OfflineRevocation", "NoError" })
  $exportable = $null
  try {
    $k = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    if ($k -is [Security.Cryptography.RSACng]) { $exportable = ($k.Key.ExportPolicy -band [Security.Cryptography.CngExportPolicies]::AllowPlaintextExport) -ne 0 -or ($k.Key.ExportPolicy -band [Security.Cryptography.CngExportPolicies]::AllowExport) -ne 0 }
  } catch { }
  $canSign = $false
  $signError = ""
  if ($trySign -and $cert.HasPrivateKey -and $IsWin) {
    $t = Join-Path ([IO.Path]::GetTempPath()) "pg-signer-test-$([guid]::NewGuid()).ps1"
    Set-Content -Path $t -Value "# Peregovorka: проверка подписи" -Encoding UTF8
    try {
      Sign-One $t $cert
      $canSign = (Get-AuthenticodeSignature $t).Status -eq "Valid"
      if (-not $canSign) { $signError = (Get-AuthenticodeSignature $t).StatusMessage }
    } catch { $signError = $_.Exception.Message } finally { Remove-Item $t -ErrorAction SilentlyContinue }
  }
  [ordered]@{
    thumbprint = $cert.Thumbprint; subject = $cert.Subject; issuer = $cert.Issuer; not_after = $cert.NotAfter.ToUniversalTime().ToString("o")
    code_signing = $eku -contains $CodeSigningOid; has_private_key = $cert.HasPrivateKey; chain_ok = ($built -or -not $hard); chain_status = ($status -join "; ")
    time_valid = ($cert.NotBefore -le (Get-Date) -and $cert.NotAfter -gt (Get-Date)); key_exportable = $exportable; can_sign = $canSign; sign_error = $signError
    trusted_on_node = $built
  }
}

function Cert-Report($cert, [bool]$trySign = $true) {
  @{ cer_b64 = [Convert]::ToBase64String($cert.Export("Cert")); check = (Test-Cert $cert $trySign) }
}

function New-OwnCert([string]$subj, [int]$years) {
  $subj = ($subj -replace '[,=+<>#;"\\]', ' ').Trim()
  if (-not $subj) { Fail "укажите название издателя" }
  $c = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=$subj, O=$subj" -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
    -KeyExportPolicy NonExportable -KeyUsage DigitalSignature -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears($years)
  if (-not $NoTrustOnNode) {
    # доверие ТОЛЬКО на этом подписывающем узле (чтобы проверять свои подписи); компьютерам сотрудников доверие раздаёт администратор
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "$($c.Thumbprint).cer"
    [IO.File]::WriteAllBytes($tmp, $c.Export("Cert"))
    Import-Certificate -FilePath $tmp -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    Import-Certificate -FilePath $tmp -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
    Remove-Item $tmp
  }
  $c
}

# ------------------------------------------------------------------------------------------------ подпись и проверка
function Sign-One([string]$file, $cert) {
  $ts = $TimestampUrl
  if (-not $ts) { try { $ts = (Read-Config).timestamp_url } catch { } }
  $st = Find-SignTool
  if ($st) {
    $a = @("sign", "/fd", "SHA256", "/sha1", $cert.Thumbprint)
    if ($cert.PSParentPath -like "*LocalMachine*") { $a += "/sm" }
    if ($ts) { $a += @("/tr", $ts, "/td", "SHA256") }
    for ($try = 1; ; $try++) {
      $out = & $st @a $file 2>&1
      if (-not $LASTEXITCODE) { break }
      # только что записанный файл может ненадолго держать антивирус — повтор, а не отказ
      if ($try -lt 4 -and ($out -join ' ') -match 'being used by another process') { Start-Sleep -Seconds (2 * $try); continue }
      Fail "signtool: $($out -join ' ')"
    }
  } else {
    $p = @{ FilePath = $file; Certificate = $cert; HashAlgorithm = "SHA256" }
    if ($ts) { $p.TimestampServer = $ts }
    $r = Set-AuthenticodeSignature @p
    if ($r.Status -ne "Valid") { Fail "подпись $file : $($r.StatusMessage)" }
  }
}

function Sig-Info([string]$file) {
  $s = Get-AuthenticodeSignature -FilePath $file
  @{ status = "$($s.Status)"; thumbprint = "$($s.SignerCertificate.Thumbprint)"; subject = "$($s.SignerCertificate.Subject)"; timestamp = "$($s.TimeStamperCertificate.Subject)" }
}

function Sha256([string]$file) { (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLower() }

# ------------------------------------------------------------------------------------------------ корпоративная сборка
function Set-MsiProperties([string]$msi, [hashtable]$props) {
  # Windows Installer COM держит файл, пока живы ВСЕ его объекты (база, представления, записи): освобождаем каждый и собираем мусор —
  # иначе signtool следом получает «file is being used by another process»
  $wi = New-Object -ComObject WindowsInstaller.Installer
  $db = $wi.GetType().InvokeMember("OpenDatabase", "InvokeMethod", $null, $wi, @($msi, 1))
  $junk = New-Object System.Collections.ArrayList
  try {
    foreach ($k in $props.Keys) {
      foreach ($sql in "DELETE FROM ``Property`` WHERE ``Property``='$k'", "INSERT INTO ``Property`` (``Property``, ``Value``) VALUES ('$k', ?)") {
        $v = $db.GetType().InvokeMember("OpenView", "InvokeMethod", $null, $db, @($sql)); [void]$junk.Add($v)
        if ($sql.StartsWith("INSERT")) {
          $rec = $wi.GetType().InvokeMember("CreateRecord", "InvokeMethod", $null, $wi, @(1)); [void]$junk.Add($rec)
          $rec.GetType().InvokeMember("StringData", "SetProperty", $null, $rec, @(1, [string]$props[$k]))
          $v.GetType().InvokeMember("Execute", "InvokeMethod", $null, $v, @($rec)) | Out-Null
        } else { $v.GetType().InvokeMember("Execute", "InvokeMethod", $null, $v, $null) | Out-Null }
        $v.GetType().InvokeMember("Close", "InvokeMethod", $null, $v, $null) | Out-Null
      }
    }
    $db.GetType().InvokeMember("Commit", "InvokeMethod", $null, $db, $null) | Out-Null
  } finally {
    foreach ($o in $junk) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($o) }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db)
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wi)
    [GC]::Collect(); [GC]::WaitForPendingFinalizers(); [GC]::Collect()
  }
}

function Get-Sources([string]$version, [string[]]$names, [string]$dir, [string]$base, [string]$pin) {
  New-Item -ItemType Directory -Force $dir | Out-Null
  $files = @("SHA256SUMS.txt") + $names
  if ($SourceDir) {
    foreach ($n in $files) { Copy-Item (Join-Path $SourceDir $n) $dir }
  } else {
    if ($base -notmatch '^https://') { Fail "источник выпусков агента должен быть https://" }
    foreach ($n in $files) {
      Invoke-WebRequest -Uri "$base/desktop-v$version/$n" -OutFile (Join-Path $dir $n) -UseBasicParsing -TimeoutSec 1800
    }
  }
  $sums = @{}
  foreach ($line in Get-Content (Join-Path $dir "SHA256SUMS.txt")) { if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+)$') { $sums[$Matches[2].Trim()] = $Matches[1].ToLower() } }
  foreach ($n in $names) {
    if (-not $sums.ContainsKey($n)) { Fail "$n нет в SHA256SUMS.txt выпуска $version — пакет не подтверждён" }
    if ((Sha256 (Join-Path $dir $n)) -ne $sums[$n]) { Fail "SHA-256 $n не совпадает с SHA256SUMS.txt — пакет изменён, подпись отменена" }
    if ($IsWin) {
      $s = Get-AuthenticodeSignature (Join-Path $dir $n)
      if ($s.Status -eq "HashMismatch") { Fail "$n повреждён: подпись издателя не сходится с содержимым" }
      if ($pin -and $s.SignerCertificate.Thumbprint -ne $pin) { Fail "$n подписан не тем издателем ($($s.SignerCertificate.Thumbprint)), ожидался $pin" }
    }
  }
}

function Invoke-Build($p, [string]$out, [scriptblock]$progress) {
  $cfg = $null; try { $cfg = Read-Config } catch { }
  $base = if ($cfg) { $cfg.release_base } else { $ReleaseBase }
  $pin = if ($cfg -and $cfg.publisher_thumbprint) { $cfg.publisher_thumbprint } else { $PublisherThumbprint }
  $v = [string]$p.version
  if ($v -notmatch '^\d{1,3}\.\d{1,3}\.\d{1,4}$') { Fail "версия клиента — X.Y.Z" }
  if ([string]$p.server_url -notmatch '^https://[a-z0-9.\-]+(:\d+)?$') { Fail "адрес сервера организации — https://имя[:порт]" }
  $cert = Find-Cert $p.thumbprint
  $chk = Test-Cert $cert $false
  if (-not $chk.code_signing) { Fail "у сертификата нет назначения «Подпись кода»" }
  if (-not $chk.time_valid) { Fail "срок действия сертификата истёк или ещё не начался" }
  if (-not $cert.HasPrivateKey) { Fail "нет закрытого ключа сертификата на этом узле" }
  $types = @($p.types)
  $names = @()
  if ($types -contains "exe") { $names += "Peregovorka-Setup-$v.exe" }
  if ($types -contains "msi") { $names += "Peregovorka-$v-x64.msi" }
  $src = Join-Path $out "src"
  & $progress "Загрузка и проверка пакетов $v"
  Get-Sources $v $names $src $base $pin
  $slug = ([Uri]$p.server_url).Host -replace '[^A-Za-z0-9.-]', '-'
  $org = ([string]$p.org_name) -replace '[|;\r\n\t]', ' '
  $upd = if ($p.update_policy -eq "channel") { "notify" } else { [string]$p.update_policy }
  $props = [ordered]@{ SERVERS = "$org|$($p.server_url)"; ALLOWADDSERVERS = $(if ($p.allow_add) { "1" } else { "0" }); AUTOSTART = [string]$p.autostart; UPDATEMODE = $upd }
  if ($p.update_url) { $props.UPDATEURL = [string]$p.update_url }
  $made = @()
  if ($types -contains "msi") {
    & $progress "Настройки организации в MSI"
    $msi = Join-Path $out "Peregovorka-$v-$slug-x64.msi"
    Copy-Item (Join-Path $src "Peregovorka-$v-x64.msi") $msi
    Set-MsiProperties $msi $props
    & $progress "Подпись MSI"
    Sign-One $msi $cert                      # подпись — ПОСЛЕ изменения пакета: дальше файл не меняется
    $made += $msi
  }
  if ($types -contains "exe") {
    & $progress "Подпись EXE"
    $exe = Join-Path $out "Peregovorka-Setup-$v-$slug.exe"
    Copy-Item (Join-Path $src "Peregovorka-Setup-$v.exe") $exe
    Sign-One $exe $cert
    $made += $exe
    # для EXE настройки организации — политиками (те же значения, что вписаны в MSI): импорт .reg или групповая политика по шаблону ADMX
    $reg = Join-Path $out "Peregovorka-$slug-policies.reg"
    $esc = { param($s) ($s -replace '\\', '\\' -replace '"', '\"') }
    $lines = @("Windows Registry Editor Version 5.00", "", "[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Peregovorka]",
      "`"Servers`"=`"$(& $esc $props.SERVERS)`"", "`"AllowAddServers`"=dword:0000000$($props.ALLOWADDSERVERS)", "`"Autostart`"=`"$($props.AUTOSTART)`"", "`"UpdateMode`"=`"$($props.UPDATEMODE)`"")
    if ($props.UPDATEURL) { $lines += "`"UpdateUrl`"=`"$(& $esc $props.UPDATEURL)`"" }
    [IO.File]::WriteAllText($reg, ($lines -join "`r`n") + "`r`n", [Text.Encoding]::Unicode)
    $made += $reg
  }
  $cer = Join-Path $out "Peregovorka-$slug-publisher.cer"
  [IO.File]::WriteAllBytes($cer, $cert.Export("Cert"))
  $made += $cer
  & $progress "Проверка подписей"
  $result = @()
  foreach ($f in $made) {
    $item = [ordered]@{ name = (Split-Path $f -Leaf); sha256 = (Sha256 $f); size = (Get-Item $f).Length }
    if ($f -match '\.(exe|msi)$') {
      $item.signature = Sig-Info $f
      if ($item.signature.thumbprint -ne $cert.Thumbprint) { Fail "$($item.name) подписан не тем сертификатом" }
      if ($item.signature.status -ne "Valid") { Fail "подпись $($item.name) не действительна на узле: $($item.signature.status)" }
      $st = Find-SignTool
      if ($st) { $o = & $st verify /pa $f 2>&1; if ($LASTEXITCODE) { Fail "signtool verify $($item.name): $($o -join ' ')" } }
    }
    $result += $item
  }
  $sums = Join-Path $out "SHA256SUMS.txt"
  ($result | ForEach-Object { "$($_.sha256)  $($_.name)" }) -join "`n" | Set-Content -Encoding ascii -NoNewline $sums
  $result += [ordered]@{ name = "SHA256SUMS.txt"; sha256 = (Sha256 $sums); size = (Get-Item $sums).Length }
  Remove-Item -Recurse -Force $src
  @{ files = $result; source = "$base/desktop-v$v" }
}

# ------------------------------------------------------------------------------------------------ цикл агента
function Invoke-Job($job) {
  $id = $job.id
  $prog = { param($t) try { Api POST "/signing/agent/jobs/$id/progress" @{ text = $t } | Out-Null } catch { } }.GetNewClosure()
  $p = $job.params
  if (-not $IsWin -and $job.kind -in "create_cert", "register_cert", "check_cert", "build") { Fail "подписывающий узел должен работать в Windows (хранилище сертификатов и Authenticode)" }
  switch ($job.kind) {
    "create_cert" {
      & $prog "Создание сертификата"
      $c = New-OwnCert $p.subject ([int]$p.years)
      return @{ ok = $true; cert = (Cert-Report $c) }
    }
    { $_ -in "register_cert", "check_cert" } {
      & $prog "Проверка сертификата"
      return @{ ok = $true; cert = (Cert-Report (Find-Cert $p.thumbprint)) }
    }
    "build" {
      $out = Join-Path ([IO.Path]::GetTempPath()) "pg-build-$id"
      Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
      New-Item -ItemType Directory -Force $out | Out-Null
      try {
        $r = Invoke-Build $p $out $prog
        & $prog "Передача пакетов"
        foreach ($f in $r.files) { Upload $id (Join-Path $out $f.name) }
        return @{ ok = $true; files = $r.files; source = $r.source }
      } finally { Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue }
    }
    default { Fail "неизвестный вид задания: $($job.kind) — агент выполняет только create_cert, register_cert, check_cert, build" }
  }
}

function Run-Loop {
  $cfg = Read-Config
  Write-Host "агент «$($cfg.name)» → $($cfg.server)"
  $wait = 5
  while ($true) {
    try {
      $r = Api POST "/signing/agent/poll" @{ version = $AgentVersion; info = (Host-Info) }
      $wait = 5
      if ($r.job) {
        Write-Host "$(Get-Date -Format s) задание $($r.job.kind) $($r.job.id)"
        try { $res = Invoke-Job $r.job } catch { $res = @{ ok = $false; error = $_.Exception.Message } }
        Api POST "/signing/agent/jobs/$($r.job.id)/finish" $res | Out-Null
        Write-Host "$(Get-Date -Format s) → $(if ($res.ok) { 'готово' } else { 'ошибка: ' + $res.error })"
        continue
      }
    } catch {
      $code = $_.Exception.Response.StatusCode.value__
      if ($code -eq 401) { Fail "сервер отклонил токен агента (агент отключён администратором?)" }
      Write-Warning "$(Get-Date -Format s) нет связи с сервером: $($_.Exception.Message)"
      $wait = [Math]::Min($wait * 2, 300)
    }
    if ($Once) { return }
    Start-Sleep -Seconds $wait
  }
}

# ------------------------------------------------------------------------------------------------ команды
switch ($Command) {
  "Enroll" {
    $srv = Assert-ServerUrl $Server
    if (-not $Code) { Fail "нужен код подключения из раздела «Подпись и корпоративные сборки»" }
    Protect-Dir $ConfigDir
    $body = @{ code = $Code; host = [Environment]::MachineName; version = $AgentVersion; info = @{ os = [Environment]::OSVersion.VersionString; powershell = $PSVersionTable.PSVersion.ToString() } }
    $r = Invoke-RestMethod -Method Post -Uri "$srv/api/v1/signing/agent/enroll" -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress))) -ContentType "application/json; charset=utf-8" -UseBasicParsing
    @{ server = $srv; agent_id = $r.agent_id; name = $r.name; release_base = $ReleaseBase.TrimEnd("/"); publisher_thumbprint = ($PublisherThumbprint -replace '[^0-9A-Fa-f]', '').ToUpper(); timestamp_url = $TimestampUrl } |
      ConvertTo-Json | Set-Content (Join-Path $ConfigDir "agent.json") -Encoding UTF8
    Save-Token $r.token
    Write-Host "агент подключён: $($r.name) ($($r.agent_id)). Запуск: .\Peregovorka-Signer.ps1 Install"
  }
  "Run" { Run-Loop }
  "Status" { Read-Config | Format-List; Api POST "/signing/agent/poll" @{ version = $AgentVersion } | Out-Null; Write-Host "связь с сервером есть" }
  "Install" {
    $script = (Resolve-Path $PSCommandPath).Path
    $dest = Join-Path $ConfigDir "Peregovorka-Signer.ps1"
    Copy-Item $script $dest -Force
    $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dest`" Run -ConfigDir `"$ConfigDir`""
    $t = New-ScheduledTaskTrigger -AtStartup
    $s = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    Register-ScheduledTask -TaskName "Peregovorka Signer" -Action $a -Trigger $t -Settings $s -User "SYSTEM" -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName "Peregovorka Signer"
    Write-Host "агент установлен и запущен (задача «Peregovorka Signer», SYSTEM)"
  }
  "Uninstall" { Unregister-ScheduledTask -TaskName "Peregovorka Signer" -Confirm:$false; Write-Host "задача удалена; сертификаты в хранилище не тронуты" }
  "ListCerts" {
    foreach ($store in "Cert:\LocalMachine\My", "Cert:\CurrentUser\My") {
      Get-ChildItem $store | Where-Object { $_.HasPrivateKey -and ($_.EnhancedKeyUsageList.ObjectId -contains $CodeSigningOid) } |
        Select-Object @{ n = "Store"; e = { $store } }, Subject, Issuer, Thumbprint, NotAfter
    }
  }
  "ImportPfx" {
    if (-not (Test-Path $Path)) { Fail "файл PFX не найден" }
    $pw = Read-Host "Пароль PFX (вводится только здесь, на узле подписи)" -AsSecureString
    $c = Import-PfxCertificate -FilePath $Path -CertStoreLocation Cert:\LocalMachine\My -Password $pw          # без -Exportable: ключ после импорта не выгрузить
    $chk = Test-Cert $c
    $chk | ConvertTo-Json
    if (-not $chk.code_signing) { Write-Warning "у сертификата нет назначения «Подпись кода» — подписывать им нельзя" }
    Write-Host "Отпечаток для раздела администрирования: $($c.Thumbprint). Файл PFX после импорта удалите надёжным способом."
  }
  "NewCert" { $c = New-OwnCert $Subject $Years; (Test-Cert $c) | ConvertTo-Json }
  "CheckCert" { (Test-Cert (Find-Cert $Thumbprint)) | ConvertTo-Json }
  "SignFiles" {
    $c = Find-Cert $Thumbprint
    foreach ($f in $Files) { Sign-One (Resolve-Path $f).Path $c; $i = Sig-Info $f; Write-Host "$f : $($i.status) $($i.subject)"; if ($i.status -ne "Valid") { Fail "подпись $f недействительна" } }
  }
  "Build" {
    $p = Get-Content $JobFile -Raw -Encoding UTF8 | ConvertFrom-Json
    New-Item -ItemType Directory -Force $OutDir | Out-Null
    $r = Invoke-Build $p (Resolve-Path $OutDir).Path { param($t) Write-Host "… $t" }
    $r.files | ForEach-Object { [pscustomobject]$_ } | Format-Table name, size, sha256 -AutoSize
  }
}
