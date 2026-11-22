@echo off
cd /d "%~dp0"
echo Construction du fichier unique OraScrapInAll...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build-distrib.ps1"
echo.
if errorlevel 1 (
  echo Echec.
  pause
  exit /b 1
)
echo.
echo Ouvre le dossier archives pour recuperer le fichier a donner.
explorer "%~dp0archives"
pause
