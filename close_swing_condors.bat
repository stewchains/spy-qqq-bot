@echo off
REM One-off: closes every condor the 30-45 day bot is tracking. Stop that bot first.
cd /d "%~dp0"
set PY=python
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import alpaca, pandas" 2>nul && set PY=.venv\Scripts\python.exe
)
%PY% close_condors.py --config config_swing.yaml
pause
