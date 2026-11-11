!include "LogicLib.nsh"

!macro OrcaDeltaPrepare SCRIPT FILELIST
  InitPluginsDir
  CreateDirectory "$PLUGINSDIR\skip"
  File "/oname=$PLUGINSDIR\orca_delta.ps1" "${SCRIPT}"
  File "/oname=$PLUGINSDIR\new_manifest.txt" "${INST_DIR}\install_manifest.txt"
  File "/oname=$PLUGINSDIR\files.txt" "${FILELIST}"
  DetailPrint "Comparing installed files with this version..."
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\orca_delta.ps1" -InstallDir "$INSTDIR" -PluginsDir "$PLUGINSDIR"'
  Pop $0
!macroend

!macro OrcaDeltaFile INDEX OUTDIR REL
  ${IfNot} ${FileExists} "$PLUGINSDIR\skip\${INDEX}"
    SetOutPath "${OUTDIR}"
    File "${INST_DIR}\${REL}"
  ${EndIf}
!macroend
