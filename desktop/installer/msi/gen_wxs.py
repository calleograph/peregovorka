"""Генерирует Peregovorka.wxs (корпоративный MSI, WiX v5+): для каждого свойства администратора — «запомненное» значение (обновление без повторной передачи
свойств их не теряет) и условный компонент, записывающий политику в HKLM\\SOFTWARE\\Policies\\Peregovorka. Запуск: python3 gen_wxs.py."""
from pathlib import Path

PROPS = [  # свойство MSI, имя значения политики, тип значения реестра
    ("SERVERS", "Servers", "string"), ("ALLOWADDSERVERS", "AllowAddServers", "integer"), ("ALLOWEDDOMAINS", "AllowedDomains", "string"),
    ("AUTOSTART", "Autostart", "string"), ("CLOSEACTION", "CloseAction", "string"), ("UPDATEMODE", "UpdateMode", "string"), ("UPDATEURL", "UpdateUrl", "string"),
    ("NOTIFICATIONS", "Notifications", "integer"), ("HIDENOTIFICATIONCONTENT", "HideNotificationContent", "integer"),
    ("PROXYMODE", "ProxyMode", "string"), ("PROXYSERVER", "ProxyServer", "string"), ("PROXYBYPASS", "ProxyBypass", "string"), ("PROXYPAC", "ProxyPac", "string"),
]

props = "\n".join(f'''    <Property Id="{p}" Secure="yes" />
    <Property Id="SAVED_{p}" Secure="yes"><RegistrySearch Id="Find{n}" Root="HKLM" Key="SOFTWARE\\Peregovorka\\Desktop\\Install" Name="{n}" Type="raw" Bitness="always64" /></Property>
    <SetProperty Id="{p}" Value="[SAVED_{p}]" After="AppSearch" Sequence="both" Condition="NOT {p} AND SAVED_{p}" />''' for p, n, _ in PROPS)
comps = "\n".join(f'''      <Component Id="Pol{n}" Condition="{p}" Bitness="always64">
        <RegistryValue Root="HKLM" Key="SOFTWARE\\Policies\\Peregovorka" Name="{n}" Type="{t}" Value="[{p}]" KeyPath="yes" />
        <RegistryValue Root="HKLM" Key="SOFTWARE\\Peregovorka\\Desktop\\Install" Name="{n}" Type="string" Value="[{p}]" />
      </Component>''' for p, n, t in PROPS)

Path(__file__).with_name("Peregovorka.wxs").write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<!-- Корпоративный установщик Peregovorka для Windows (WiX v5+). Создано gen_wxs.py — правьте генератор, а не этот файл.
     Сборка: wix build Peregovorka.wxs -arch x64 -bindpath unpacked=<release/win-unpacked> -d Version=X.Y.Z -o Peregovorka-X.Y.Z-x64.msi
     Свойства администратора (все необязательны; учётные данные пользователей в MSI не передаются): docs/DESKTOP_ADMIN.md. -->
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Package Name="Peregovorka" Manufacturer="Peregovorka" Version="$(Version)" UpgradeCode="7b4f2a1e-5c3d-4e8f-9a6b-2d1c0e9f8a73" Scope="perMachine" Compressed="yes" Language="1033">
    <MajorUpgrade DowngradeErrorMessage="A newer version of Peregovorka is already installed." Schedule="afterInstallInitialize" />
    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />
    <Icon Id="AppIcon.exe" SourceFile="!(bindpath.unpacked)\\Peregovorka.exe" />
    <Property Id="ARPPRODUCTICON" Value="AppIcon.exe" />
    <Property Id="ARPNOMODIFY" Value="1" />

    <!-- свойство не передано при обновлении — берётся значение, записанное при первой установке -->
{props}
    <SetProperty Id="RUNARGS" Value=" --hidden" After="SetAUTOSTART" Sequence="both" Condition="AUTOSTART=&quot;tray&quot;" />

    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="INSTALLFOLDER" Name="Peregovorka">
        <Files Include="!(bindpath.unpacked)\\**" />
      </Directory>
    </StandardDirectory>

    <StandardDirectory Id="ProgramMenuFolder">
      <Component Id="StartMenuShortcut" Bitness="always64">
        <Shortcut Id="StartShortcut" Name="Peregovorka" Target="[INSTALLFOLDER]Peregovorka.exe" WorkingDirectory="INSTALLFOLDER">
          <ShortcutProperty Key="System.AppUserModel.ID" Value="org.peregovorka.desktop" />
        </Shortcut>
        <RegistryValue Root="HKLM" Key="SOFTWARE\\Peregovorka\\Desktop" Name="StartMenuShortcut" Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </StandardDirectory>

    <ComponentGroup Id="System" Directory="INSTALLFOLDER">
      <!-- признак корпоративной установки: обновления выполняет администратор, а не приложение -->
      <Component Id="InstallType" Bitness="always64">
        <RegistryValue Root="HKLM" Key="SOFTWARE\\Peregovorka\\Desktop" Name="InstallType" Type="string" Value="msi" KeyPath="yes" />
      </Component>
      <Component Id="Protocol" Bitness="always64">
        <RegistryKey Root="HKLM" Key="SOFTWARE\\Classes\\peregovorka">
          <RegistryValue Type="string" Value="URL:Peregovorka" KeyPath="yes" />
          <RegistryValue Name="URL Protocol" Type="string" Value="" />
          <RegistryValue Key="DefaultIcon" Type="string" Value="[INSTALLFOLDER]Peregovorka.exe,0" />
          <RegistryValue Key="shell\\open\\command" Type="string" Value="&quot;[INSTALLFOLDER]Peregovorka.exe&quot; &quot;%1&quot;" />
        </RegistryKey>
      </Component>
      <!-- автозапуск для всех пользователей: tray — свёрнутым (ключ --hidden), window — с окном -->
      <Component Id="RunAtLogon" Condition="AUTOSTART=&quot;tray&quot; OR AUTOSTART=&quot;window&quot;" Bitness="always64">
        <RegistryValue Root="HKLM" Key="SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run" Name="Peregovorka" Type="string" Value="&quot;[INSTALLFOLDER]Peregovorka.exe&quot;[RUNARGS]" KeyPath="yes" />
      </Component>
{comps}
    </ComponentGroup>

    <Feature Id="Main" Title="Peregovorka" Level="1">
      <ComponentGroupRef Id="System" />
      <ComponentRef Id="StartMenuShortcut" />
    </Feature>
  </Package>
</Wix>
''', encoding="utf-8")
