; Дополнения установщика EXE (NSIS, electron-builder).
; Удаление: запись автозапуска текущего пользователя удаляется всегда; настройки, журналы и сохранённые входы — по выбору человека
; (при тихом удалении /S остаются: повторная установка подхватит их без новой настройки).

!macro customUnInstall
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Peregovorka"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "Peregovorka"
  ${ifNot} ${isUpdated}
    IfSilent keep_data
    MessageBox MB_YESNO|MB_ICONQUESTION "Удалить также настройки Peregovorka, подключённые организации и сохранённые входы на этом компьютере?$\r$\n$\r$\nПереписка и материалы на серверах не изменятся." /SD IDNO IDNO keep_data
      RMDir /r "$APPDATA\Peregovorka"
    keep_data:
  ${endIf}
!macroend
