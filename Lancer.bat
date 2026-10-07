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
"%PY%" -c "import flask,waitress,pandas,openpyxl,selenium,pymupdf" 1>nul 2>nul
if errorlevel 1 (
  echo Installation des bibliotheques, patientez...
  "%PY%" -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Echec de l'installation.
    pause
    exit /b 1
  )
)
netstat -ano | findstr /R /C:":5001 .*LISTENING" >nul
if not errorlevel 1 (
  "%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/api/health', timeout=3).read()" 1>nul 2>nul
  if errorlevel 1 (
    echo Une ancienne fenetre utilise encore le port 5001.
    echo Fermez la fenetre OraScrapInAll, puis relancez ce fichier.
    pause
    exit /b 1
  )
  echo L'application est deja ouverte. Ouverture de la page.
  start "" "http://127.0.0.1:5001"
  exit /b 0
)
start "OraScrapInAll" "%PY%" "%~dp0app\app.py"
ping -n 3 127.0.0.1 >nul
start "" "http://127.0.0.1:5001"
