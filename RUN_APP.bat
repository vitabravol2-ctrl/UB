@echo off
cd /d %~dp0
if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
if exist requirements.txt (
  echo [INFO] requirements.txt found
  pip install -r requirements.txt
) else (
  echo [WARNING] requirements.txt not found
)
python main.py
if errorlevel 1 (
  echo Application exited with error.
)
pause
