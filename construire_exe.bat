@echo off
rem Construit le programme portable (exe) : double-clic.
rem Premiere fois : cree l'environnement exe\venv (Python 3.11) et ses paquets.
rem Options : --docs = refaire seulement documents, licences et archive.
cd /d "%~dp0"
if not exist "exe\venv\Scripts\python.exe" (
  echo Creation de l'environnement de construction exe\venv ...
  py -3.11 -m venv exe\venv || goto :erreur
  exe\venv\Scripts\python.exe -m pip install --upgrade pip || goto :erreur
  exe\venv\Scripts\python.exe -m pip install -r exe\requirements.txt || goto :erreur
)
exe\venv\Scripts\python.exe construire_exe.py %* || goto :erreur
echo.
echo Termine : programme dans exe\dist, archive a publier dans exe\
pause
exit /b 0
:erreur
echo.
echo ERREUR pendant la construction (voir les messages ci-dessus).
pause
exit /b 1
