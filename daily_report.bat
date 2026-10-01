@echo off
REM Double-click: build today's report for BOTH bots (they also do this on their own after the close).
cd /d "%~dp0"
set PY=python
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import alpaca, pandas" 2>nul && set PY=.venv\Scripts\python.exe
)
echo === 0DTE bot (account 1) ===
%PY% daily_report.py
echo.
echo === 30-45 day bot (account 2) ===
%PY% daily_report.py --config config_swing.yaml
echo.
pause
