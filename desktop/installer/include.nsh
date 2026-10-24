; Дополнения установщика EXE (NSIS, electron-builder).
; Удаление: запись автозапуска текущего пользователя удаляется всегда; настройки, журналы и сохранённые входы — по выбору человека
; (при тихом удалении /S остаются: повторная установка подхватит их без новой настройки).

; Поверх корпоративной установки MSI установщик EXE не ставится: иначе на компьютере окажутся две копии программы с разными обновлениями.
; Код выхода 2 — для систем развёртывания.
!macro customInit
  ReadRegStr $0 HKLM64 "SOFTWARE\Peregovorka\Desktop" "InstallType"
  ${if} $0 == "msi"
    IfSilent +2
      MessageBox MB_OK|MB_ICONEXCLAMATION "Peregovorka уже установлена на этом компьютере администратором (пакет MSI).$\r$\n$\r$\nОбновления для неё устанавливает администратор — этот установщик не нужен."
    SetErrorLevel 2
    Quit
  ${endIf}
!macroend

; Ссылки peregovorka:// работают сразу после установки (не только после первого запуска программы). SHCTX — ветка текущего пользователя или машины
; в зависимости от вида установки.
!macro customInstall
  WriteRegStr SHCTX "Software\Classes\peregovorka" "" "URL:Peregovorka"
  WriteRegStr SHCTX "Software\Classes\peregovorka" "URL Protocol" ""
  WriteRegStr SHCTX "Software\Classes\peregovorka\DefaultIcon" "" "$INSTDIR\Peregovorka.exe,0"
  WriteRegStr SHCTX "Software\Classes\peregovorka\shell\open\command" "" '"$INSTDIR\Peregovorka.exe" "%1"'
!macroend

!macro customUnInstall
  ${ifNot} ${isUpdated}
    DeleteRegKey SHCTX "Software\Classes\peregovorka"
  ${endIf}
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Peregovorka"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "Peregovorka"
  ${ifNot} ${isUpdated}
    IfSilent keep_data
    MessageBox MB_YESNO|MB_ICONQUESTION "Удалить также настройки Peregovorka, подключённые организации и сохранённые входы на этом компьютере?$\r$\n$\r$\nПереписка и материалы на серверах не изменятся." /SD IDNO IDNO keep_data
      RMDir /r "$APPDATA\Peregovorka"
    keep_data:
  ${endIf}
!macroend
