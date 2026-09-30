@echo off
REM Windows: double-click to start the bot.
REM Uses the .venv virtual environment if its packages are installed, otherwise your regular Python.
cd /d "%~dp0"
set PY=python
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import alpaca, pandas" 2>nul && set PY=.venv\Scripts\python.exe
)
echo Using %PY%
%PY% run_bot.py
pause
