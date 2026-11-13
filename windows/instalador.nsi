; Instalador de Luces Navideñas para Windows (NSIS 3, Unicode).
; Lo llama windows/build.py con /DVERSION /DSRCDIR /DOUTFILE /DICON /DLICENSE.
;
; Instalación por usuario (sin permisos de administrador), en la misma carpeta y con las mismas
; claves que la 1.4.2, así una versión nueva reemplaza limpiamente a la anterior.

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define APPNAME "Luces Navideñas"
!define EXE "LucesNavidenas.exe"
!define REGKEY "Software\LucesNavidenas"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\LucesNavidenas"
!define RUNKEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define MUTEX "Local\LucesNavidenas-instancia"

Name "${APPNAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\LucesNavidenas"
InstallDirRegKey HKCU "${REGKEY}" "InstallDir"
BrandingText "${APPNAME} ${VERSION}"

VIProductVersion "${VERSION}.0.0"
VIAddVersionKey /LANG=1034 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=1034 "FileDescription" "Instalador de ${APPNAME}"
VIAddVersionKey /LANG=1034 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1034 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1034 "LegalCopyright" "GPL-3.0-or-later"

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_COMPONENTSPAGE_SMALLDESC
!define MUI_WELCOMEPAGE_TITLE "Bienvenido a ${APPNAME}"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instalará ${APPNAME} ${VERSION}: bombillitos de Navidad que cuelgan del borde de tu pantalla, inspirados en el clásico Holiday Lights de Windows 95.$\r$\n$\r$\nSi tienes una versión anterior abierta, se cerrará y se reemplazará; tus ajustes se conservan."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Encender las luces ahora"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE}"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Spanish"

; Cierra la copia que esté abierta y espera a que termine (hasta ~10 s).
!macro CERRAR_PROGRAMA UN
Function ${UN}CerrarPrograma
  IfFileExists "$INSTDIR\${EXE}" 0 fin
    ; el ejecutable instalado sabe pedirle a su propia versión que se cierre (también la 1.4.2)
    ExecWait '"$INSTDIR\${EXE}" --salir'
  StrCpy $1 0
  espera:
    System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "${MUTEX}") p .r0'
    ${If} $0 P<> 0
      System::Call 'kernel32::CloseHandle(p r0)'
      IntOp $1 $1 + 1
      ${If} $1 < 40
        Sleep 250
        Goto espera
      ${EndIf}
    ${EndIf}
  Sleep 500  ; que Windows suelte los archivos del programa
  fin:
FunctionEnd
!macroend
!insertmacro CERRAR_PROGRAMA ""
!insertmacro CERRAR_PROGRAMA "un."

Section "${APPNAME} (necesario)" SecMain
  SectionIn RO
  Call CerrarPrograma
  ; las bibliotecas cambian entre versiones: no dejar restos de la anterior
  RMDir /r "$INSTDIR\_internal"
  SetOutPath "$INSTDIR"
  File /r "${SRCDIR}\*.*"
  WriteUninstaller "$INSTDIR\Desinstalar.exe"

  WriteRegStr HKCU "${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "URLInfoAbout" "https://github.com/1985a/luces-navidenas"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegStr HKCU "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\Desinstalar.exe" /S'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${EXE}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\Encender o apagar las luces.lnk" "$INSTDIR\${EXE}" "--alternar"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\Desinstalar ${APPNAME}.lnk" "$INSTDIR\Desinstalar.exe"
SectionEnd

Section "Acceso directo en el escritorio" SecDesktop
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXE}"
SectionEnd

Section /o "Encender las luces al iniciar Windows" SecAutostart
  WriteRegStr HKCU "${RUNKEY}" "LucesNavidenas" '"$INSTDIR\${EXE}" --oculto'
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "El programa y sus accesos en el menú Inicio."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Un acceso directo para abrir las opciones."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecAutostart} "Las luces se encienden solas cada vez que inicias sesión. Se puede cambiar luego en las opciones."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  Call un.CerrarPrograma
  DeleteRegValue HKCU "${RUNKEY}" "LucesNavidenas"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "${REGKEY}"
  ; los ajustes y la caché son del usuario: solo se borran si lo pide (en modo silencioso, no)
  MessageBox MB_YESNO|MB_ICONQUESTION "¿Borrar también tus ajustes de ${APPNAME}?" /SD IDNO IDNO conservar
    RMDir /r "$APPDATA\LucesNavidenas"
    RMDir /r "$LOCALAPPDATA\LucesNavidenas"
  conservar:
SectionEnd
