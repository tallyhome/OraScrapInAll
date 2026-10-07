@echo off
cd /d "%~dp0"
set PYTHONNOUSERSITE=1
set ORASCRAP_PORTABLE=1
set PY=%~dp0runtime\python\python.exe
if not exist "%PY%" (
  echo Python portable introuvable dans runtime\python.
  echo Le dossier runtime doit rester a cote de ce fichier.
  pause
  exit /b 1
)
"%PY%" -c "import flask,pandas,openpyxl,selenium,pymupdf" 1>nul 2>nul
if errorlevel 1 (
  echo Installation des bibliotheques, patientez...
  "%PY%" -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Echec de l'installation.
    pause
    exit /b 1
  )
)
"%PY%" "%~dp0app\app.py"
if errorlevel 1 pause
