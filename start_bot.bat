@echo off
REM Windows: double-click to start the bot (or let Task Scheduler start it each morning).
REM Uses the .venv virtual environment if its packages are installed, otherwise your regular Python.
REM If a copy is already running, this one exits right away.
cd /d "%~dp0"
set PY=python
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import alpaca, pandas" 2>nul && set PY=.venv\Scripts\python.exe
)
echo Using %PY%
%PY% run_bot.py
REM Keep the window open only if something went wrong, so you can read the error.
if errorlevel 1 pause
