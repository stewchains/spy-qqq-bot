@echo off
REM Windows: double-click to start the 30-45 day iron condor bot.
REM If this bot is already running, this copy exits right away.
cd /d "%~dp0"
set PY=python
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import alpaca, pandas" 2>nul && set PY=.venv\Scripts\python.exe
)
echo Using %PY%
%PY% run_bot.py --config config_swing.yaml
if errorlevel 1 pause
